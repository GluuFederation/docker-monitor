"""
Monitor tool for Gluu
Author : Mohammad Abudayyeh
"""
import logging.config
import sys
import logging
import time
import subprocess
import json
from kubernetes import client, config
from ldap3 import Server, Connection, MODIFY_REPLACE
from pygluu.containerlib import get_manager
from pygluu.containerlib.utils import decode_text
from pygluu.containerlib.persistence.couchbase import get_couchbase_user
from pygluu.containerlib.persistence.couchbase import get_couchbase_password
from pygluu.containerlib.persistence.couchbase import CouchbaseClient
from settings import LOGGING_CONFIG

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger("monitor")


def subprocess_cmd(command):
    """Execute command"""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, shell=True)
    proc_stdout = process.communicate()[0].strip()
    return proc_stdout


class Kubernetes(object):
    def __init__(self):
        config_loaded = False
        try:
            config.load_incluster_config()
            config_loaded = True
        except config.config_exception.ConfigException:
            logger.warning("Unable to load in-cluster configuration; trying to load from Kube config file")
            try:
                config.load_kube_config()
                config_loaded = True
            except (IOError, config.config_exception.ConfigException) as exc:
                logger.warning("Unable to load Kube config; reason={}".format(exc))

        if not config_loaded:
            logger.error("Unable to load in-cluster or Kube config")
            sys.exit(1)

        self.core_cli = client.CoreV1Api()
        self.core_cli.api_client.configuration.assert_hostname = False

    def list_pod_name_by_label(self, namespace="default", app_label=None):
        """List pods names with app label in namespace"""
        try:
            pods_name = []
            response = self.core_cli.list_namespaced_pod(namespace=namespace, label_selector=app_label, watch=False)
            number_of_pods = len(response.items)
            for i in range(number_of_pods):
                pods_name.append(response.items[i].metadata.name)
            return pods_name
        except client.rest.ApiException as e:
            logger.exception(e)

    def read_namespaced_pod_status_condition(self, name, namespace="default"):
        """Read pod status with name in namespace"""
        try:
            response = self.core_cli.read_namespaced_pod_status(name=name, namespace=namespace)
            return response
        except client.rest.ApiException as e:
            logger.exception(e)

    def check_pods_statuses(self, namespace="default", app_label=None):
        """Loop through pod names and check statuses"""
        pods_responses = []
        pods_name = self.list_pod_name_by_label(namespace, app_label)
        for pod_name in pods_name:
            pod_response = self.read_namespaced_pod_status_condition(name=pod_name, namespace=namespace)
            pods_responses.append(pod_response)
        return pods_responses

    def get_namespaces(self):
        """List all namespaces"""
        try:
            return self.core_cli.list_namespace(pretty="pretty")
        except client.rest.ApiException as e:
            logger.exception(e)
            return False

    def list_nodes(self):
        """List all nodes"""
        try:
            nodes_list = self.core_cli.list_node(pretty="pretty")
            logger.info("Getting list of nodes")
            return nodes_list
        except client.rest.ApiException as e:
            logger.exception(e)
            return False

    def read_node(self, name):
        """Read node information"""
        try:
            node_data = self.core_cli.read_node(name)
            logger.info("Getting node {} data".format(name))
            return node_data
        except client.rest.ApiException as e:
            logger.exception(e)
            return False


class BaseBackend(object):
    def get_configuration(self):
        raise NotImplementedError

    def update_configuration(self):
        raise NotImplementedError


class LDAPBackend(BaseBackend):
    def __init__(self, host, user, password):
        ldap_server = Server(host, port=1636, use_ssl=True)
        self.backend = Connection(ldap_server, user, password)

    def get_configuration(self):
        with self.backend as conn:
            conn.search(
                "ou=configuration,o=gluu",
                '(objectclass=gluuConfiguration)',
                attributes=['oxTrustCacheRefreshServerIpAddress',
                            'gluuVdsCacheRefreshEnabled'],
            )

            if not conn.entries:
                return {}

            entry = conn.entries[0]
            config = {
                "id": entry.entry_dn,
                "oxTrustCacheRefreshServerIpAddress": entry["oxTrustCacheRefreshServerIpAddress"][0],
                "gluuVdsCacheRefreshEnabled": entry["gluuVdsCacheRefreshEnabled"][0],
            }
            return config

    def update_configuration(self, id_, ip):
        with self.backend as conn:
            conn.modify(
                id_,
                {'oxTrustCacheRefreshServerIpAddress': [(MODIFY_REPLACE, [ip])]}
            )
            result = {
                "success": conn.result["description"] == "success",
                "message": conn.result["message"],
            }
            return result


class CouchbaseBackend(BaseBackend):
    def __init__(self, host, user, password):
        self.backend = CouchbaseClient(host, user, password)

    def get_configuration(self):
        req = self.backend.exec_query(
            "SELECT oxTrustCacheRefreshServerIpAddress, gluuVdsCacheRefreshEnabled "
            "FROM `gluu` "
            "USE KEYS 'configuration'"
        )

        if not req.ok:
            return {}

        config = req.json()["results"][0]

        if not config:
            return {}

        config.update({"id": "configuration"})
        return config

    def update_configuration(self, id_, ip):
        req = self.backend.exec_query(
            "UPDATE `gluu` "
            "USE KEYS '{0}' "
            "SET oxTrustCacheRefreshServerIpAddress='{1}' "
            "RETURNING oxTrustCacheRefreshServerIpAddress".format(id_, ip)
        )

        result = {
            "success": req.ok,
            "message": req.text,
        }
        return result


class Monitor(object):
    monitor_settings = dict(
        CASA=dict(
            CASA_NUMBER_OF_RUNNING_PODS=0,
            CASA_TOTAL_RUNNING_TIME=0,
            CASA_POD_NAME=dict(),
        ),
        OXAUTH=dict(
            OXAUTH_NUMBER_OF_RUNNING_PODS=0,
            OXAUTH_TOTAL_RUNNING_TIME=0,
            OXAUTH_POD_NAME=dict(),

        ),
        OXD_SERVER=dict(
            OXD_SERVER_NUMBER_OF_RUNNING_PODS=0,
            OXD_SERVER_TOTAL_RUNNING_TIME=0,
            OXD_SERVER_POD_NAME=dict(),

        ),
        OXPASSPORT=dict(
            OXPASSPORT_NUMBER_OF_RUNNING_PODS=0,
            OXPASSPORT_TOTAL_RUNNING_TIME=0,
            OXPASSPORT_POD_NAME=dict(),
        ),
        RADIUS=dict(
            RADIUS_NUMBER_OF_RUNNING_PODS=0,
            RADIUS_TOTAL_RUNNING_TIME=0,
            RADIUS_POD_NAME=dict(),
        ),
        REDIS=dict(
            REDIS_NUMBER_OF_RUNNING_PODS=0,
            REDIS_TOTAL_RUNNING_TIME=0,
            REDIS_POD_NAME=dict(),
        ),
        EFS=dict(
            EFS_NUMBER_OF_RUNNING_PODS=0,
            EFS_TOTAL_RUNNING_TIME=0,
            EFS_POD_NAME=dict(),
        ),
        KEY_ROTATION=dict(
            KEY_ROTATION_NUMBER_OF_RUNNING_PODS=0,
            KEY_ROTATION_TOTAL_RUNNING_TIME=0,
            KEY_ROTATION_POD_NAME=dict(),
        ),
        OPENDJ=dict(
            OPENDJ_NUMBER_OF_RUNNING_PODS=0,
            OPENDJ_TOTAL_RUNNING_TIME=0,
            OPENDJ_POD_NAME=dict(),
        ),
        OXTRUST=dict(
            OXTRUST_NUMBER_OF_RUNNING_PODS=0,
            OXTRUST_TOTAL_RUNNING_TIME=0,
            OXTRUST_POD_NAME=dict(),
        ),
        OXSHIBBOLETH=dict(
            OXSHIBBOLETH_NUMBER_OF_RUNNING_PODS=0,
            OXSHIBBOLETH_TOTAL_RUNNING_TIME=0,
            OXSHIBBOLETH_POD_NAME=dict(),
        ),
        CONFIG=dict(
            CONFIG_NUMBER_OF_RUNNING_PODS=0,
            CONFIG_TOTAL_RUNNING_TIME=0,
            CONFIG_POD_NAME=dict(),
        ),
        PERSISTENCE=dict(
            PERSISTENCE_NUMBER_OF_RUNNING_PODS=0,
            PERSISTENCE_TOTAL_RUNNING_TIME=0,
            PERSISTENCE_POD_NAME=dict(),
        ),
        CR_ROTATE=dict(
            CR_ROTATE_NUMBER_OF_RUNNING_PODS=0,
            CR_ROTATE_TOTAL_RUNNING_TIME=0,
            CR_ROTATE_POD_NAME=dict(),
        ),
        NFS=dict(
            NFS_NUMBER_OF_RUNNING_PODS=0,
            NFS_TOTAL_RUNNING_TIME=0,
            NFS_POD_NAME=dict(),
        ),
    )

    def __init__(self):
        self.settings = self.monitor_settings
        self.kubernetes = Kubernetes()
        self.gluu_namespace = self.detect_gluu_namespace()

        while True:
            time.sleep(5)
            self.analyze_app_info()

    def write_variables_to_file(self):
        """Write settings out to a file
        """
        with open('./monitor_settings.json', 'w+') as file:
            json.dump(self.settings, file, indent=2)

    def get_settings(self):
        """Get merged settings (default and custom settings from local Python file).
        """
        filename = "./monitor_settings.json"
        try:
            with open(filename) as f:
                custom_settings = json.load(f)
            self.settings.update(custom_settings)
        except FileNotFoundError:
            pass

    def analyze_nodes_info(self):
        """ Get the ips, zones, and names of all the nodes"""
        node_ip_list = []
        node_zone_list = []
        node_name_list = []
        node_list = self.kubernetes.list_nodes().items
        for node in node_list:
            node_name = node.metadata.name
            node_addresses = self.kubernetes.read_node(name=node_name).status.addresses
            try:
                # if minikube or microk8s
                for add in node_addresses:
                    if add.type == "InternalIP":
                        ip = add.address
                        node_ip_list.append(ip)
            except KeyError:
                # Cloud deployments
                for add in node_addresses:
                    if add.type == "ExternalIP":
                        ip = add.address
                        node_ip_list.append(ip)
                node_zone = node.metadata.labels["failure-domain.beta.kubernetes.io/zone"]
                node_zone_list.append(node_zone)
                node_name_list.append(node_name)

    def analyze_app_info(self):
        """Gets pods info"""
        app_labels_haeders = dict(
            # Deployments
            CASA="app=casa",
            OXAUTH="app=oxauth",
            OXD_SERVER="app=oxd-server",
            OXPASSPORT="app=oxpassport",
            RADIUS="app=radius",
            REDIS="app=redis",
            EFS="app=efs-provisioner",
            KEY_ROTATION="app=key-rotation",
            # Statefulsets
            OPENDJ="app=opendj",
            OXTRUST="app=oxtrust",
            OXSHIBBOLETH="app=oxshibboleth",
            # Job labels
            CONFIG="app=config-init-load",
            PERSISTENCE="app=persistence-load",
            # Daemonset
            CR_ROTATE="app=cr-rotate",
            # Replication Controller
            NFS="app=nfs-server",
        )
        for name, label in app_labels_haeders.items():
            # oxTrust
            responses = self.kubernetes.check_pods_statuses(self.gluu_namespace, label)
            number_of_running_pods = 0
            total_time_of_all_running_pods = self.settings[name][name + "_TOTAL_RUNNING_TIME"]
            for response in responses:
                if response.status.phase == "Running":
                    number_of_running_pods += 1
                    total_time_of_all_running_pods += 5
                    pod_name = response.metadata.name
                    pod_ip = response.status.pod_ip
                    node_name = response.spec.node_name

                    self.settings[name][name + "_POD_NAME"][pod_name] = dict()
                    self.settings[name][name + "_POD_NAME"][pod_name]["NODE"] = node_name
                    self.settings[name][name + "_POD_NAME"][pod_name]["IP"] = pod_ip
            self.settings[name][name + "_TOTAL_RUNNING_TIME"] = total_time_of_all_running_pods
            self.settings[name][name + "_NUMBER_OF_RUNNING_PODS"] = number_of_running_pods

            print("App: ", name)
            print("Total time for all replicas of " + name + ": "
                  + str(self.settings[name][name + "_TOTAL_RUNNING_TIME"]) + " secs")
            print("Total number of running replicas of " + name + ": "
                  + str(self.settings[name][name + "_NUMBER_OF_RUNNING_PODS"]) + " pods")
            for k, v in self.settings[name][name + "_POD_NAME"].items():
                print("  - Pod name: " + k)
                print("    * Pod Ip: " + str(self.settings[name][name + "_POD_NAME"][k]["IP"]))
                print("    * Pod Node location: " + self.settings[name][name + "_POD_NAME"][k]["NODE"])
            print("------------------------------------------------------------------------------------")
        self.write_variables_to_file()

    def detect_gluu_namespace(self):
        while True:
            namespaces = self.kubernetes.get_namespaces()
            for namespace in namespaces.items:
                # Detect oxauth in namespace
                pod_oxauth_name = self.kubernetes.list_pod_name_by_label(namespace.metadata.name, "app=oxauth")
                # Detect oxtrust in namespace
                pod_oxtrust_name = self.kubernetes.list_pod_name_by_label(namespace.metadata.name, "app=oxtrust")
                if pod_oxauth_name and pod_oxtrust_name:
                    return namespace.metadata.name


def main():
    try:
        Monitor()
    except KeyboardInterrupt:
        logger.info("\n[I] Canceled by user; exiting ...")


if __name__ == "__main__":
    main()
