#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
 License terms and conditions:
 https://www.gluu.org/license/enterprise-edition/
"""

from kubernetes import client, config
import sys
import logging
import time
from pathlib import Path
import subprocess
import json
from yaspin import yaspin


logger = logging.getLogger("gluu-kubernetes-api")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
fmt = logging.Formatter('%(levelname)s - %(asctime)s - %(message)s')
ch.setFormatter(fmt)
logger.addHandler(ch)


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
    )

    def __init__(self):
        self.settings = self.monitor_settings
        self.kubernetes = Kubernetes()
        self.gluu_namespace = self.detect_gluu_namespace()

        with yaspin(text="Printing Gluu Metrics".format(name), color="cyan") as sp:
            self.sp = sp
            while True:
                time.sleep(5)
                self.analyze_app_info()

    def write_variables_to_file(self):
        """Write settings out to a file
        """
        with open(Path('./monitor_settings.json'), 'w+') as file:
            json.dump(self.settings, file, indent=2)

    def get_settings(self):
        """Get merged settings (default and custom settings from local Python file).
        """
        filename = Path("./monitor_settings.json")
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

            # task 1
            self.sp.write("Printing App: {} metrics".format(name))
            time.sleep(1)
            self.sp.write("App: ", name)
            self.sp.write("Total time for all replicas of {} : {} secs".format(name, str(self.settings[name][name + "_TOTAL_RUNNING_TIME"])))
            self.sp.write("Total number of running replicas of {} : {} pods".format(name, str(self.settings[name][name + "_NUMBER_OF_RUNNING_PODS"])))
            for k, v in self.settings[name][name + "_POD_NAME"].items():
                self.sp.write("  - Pod name: {}".format(k))
                self.sp.write("    * Pod Ip: {}".format(str(self.settings[name][name + "_POD_NAME"][k]["IP"])))
                self.sp.write("    * Pod Node location: ".format(self.settings[name][name + "_POD_NAME"][k]["NODE"]))
            # finalize
            self.sp.ok("✔")

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
        print("\n[I] Canceled by user; exiting ...")


if __name__ == "__main__":
    main()
