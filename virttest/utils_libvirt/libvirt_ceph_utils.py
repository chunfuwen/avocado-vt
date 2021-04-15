"""
High-level libvirt ceph utility functions.

This module is meant to reduce code size
by performing common ceph setup procedures.

:copyright: 2021 Red Hat Inc.
"""

import logging
import os

from avocado.core import exceptions
from avocado.utils import process

from virttest import ceph
from virttest import data_dir
from virttest import utils_package
from virttest import virsh

from virttest.utils_test import libvirt

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk


# Holds reference to files for removal during cleanup if they exist
_FILES_FOR_CLEANUP = []


class _Config:
    """ Class holding parameters """

    def __init__(self, params):
        """
        :param params: test parameters
        """

        # Device related configurations
        self.device_format = params.get("virt_disk_device_format", "raw")
        self.device_bus = params.get("virt_disk_device_bus", "virtio")
        self.device = params.get("virt_disk_device", "disk")
        self.device_target = params.get("virt_disk_device_target", "vdb")
        self.hotplug = params.get("virt_disk_device_hotplug", "no") == "yes"
        self.attach_option = params.get("virt_device_attach_option", "--live")
        self.keep_raw_image_as = params.get("keep_raw_image_as", "no") == "yes"

        # Ceph related configurations
        self.ceph_mon_ip = params.get("ceph_mon_ip", "EXAMPLE_MON_HOST")
        self.ceph_host_port = params.get("ceph_host_port", "EXAMPLE_PORTS")
        self.ceph_disk_name = params.get("ceph_disk_name",
                                         "EXAMPLE_SOURCE_NAME")
        self.ceph_client_name = params.get("ceph_client_name")
        self.ceph_client_key = params.get("ceph_client_key")
        self.ceph_auth_user = params.get("ceph_auth_user")
        self.ceph_auth_key = params.get("ceph_auth_key")
        self.auth_sec_usage_type = params.get("ceph_auth_sec_usage_type",
                                              "ceph")
        self.storage_size = params.get("storage_size", "1G")
        self.img_file = params.get("ceph_image_file")
        self.key_file = os.path.join(data_dir.get_tmp_dir(), "ceph.key")
        _FILES_FOR_CLEANUP.append(self.key_file)

        self.key_opt = ""
        self.is_local_img_file = not self.img_file
        self.rbd_key_file = None

        # Prepare a blank params to confirm if delete
        # the configuration at the end of the test
        self.ceph_cfg = ""
        self.disk_auth_dict = None

        self.auth_sec_uuid = None

        # Create config if it doesn't exist
        _FILES_FOR_CLEANUP.append(ceph.create_config_file(self.ceph_mon_ip))

        self.define_auth_key()

    def define_auth_config_and_log(self):
        """
        If enable auth, prepare device source
        :return: None
        """

        if self.is_auth_case():
            auth_sec_dict = {"sec_usage": self.auth_sec_usage_type,
                             "sec_name": "ceph_auth_secret"}
            self.auth_sec_uuid = libvirt.create_secret(auth_sec_dict)
            virsh.secret_set_value(self.auth_sec_uuid, self.ceph_auth_key,
                                   debug=True)
            self.disk_auth_dict = {"auth_user": self.ceph_auth_user,
                                   "secret_type": self.auth_sec_usage_type,
                                   "secret_uuid": self.auth_sec_uuid}
            device_source = ("rbd:%s:mon_host=%s:keyring=%s" %
                             (self.ceph_disk_name,
                              self.ceph_mon_ip,
                              self.key_file))
        else:
            device_source = "rbd:%s:mon_host=%s" % (self.ceph_disk_name,
                                                    self.ceph_mon_ip)
        logging.debug("device source is: %s", device_source)

    def define_auth_key(self):
        """
        If enable auth, prepare a local file to save key

        :return: None
        """

        if self.is_auth_case():
            with open(self.key_file, 'w') as f:
                f.write("[%s]\n\tkey = %s\n" %
                        (self.ceph_client_name, self.ceph_client_key))
            self.key_opt = "--keyring %s" % self.key_file
            self.rbd_key_file = self.key_file

    def is_auth_case(self):
        """
        If this test case involves auth

        :return: True if auth is involved, else False
        """

        return self.ceph_client_name and self.ceph_client_key


def create_or_cleanup_ceph_backend_vm_disk(vm, params, is_setup=True):
    """
    Setup vm ceph disk with given parameters

    :param vm: the vm object
    :param params: dict, dict include setup vm disk xml configurations
    :param is_setup: one parameter indicate whether setup or clean up
    """

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    logging.debug("original xml is: %s", vmxml)

    if not utils_package.package_install(["ceph-common"]):
        raise exceptions.TestError("Failed to install ceph-common")

    cfg = _Config(params)

    if is_setup:
        cfg.define_auth_config_and_log()
        _remove_image(cfg)
        _create_image(cfg, vm.name)
        _update_vm(cfg, vm.name)
    else:
        _remove_image(cfg)
        _cleanup_files(cfg)
        _cleanup_secret(cfg)


def _cleanup_secret(cfg):
    """ Undefines secret """

    if cfg.auth_sec_uuid:
        virsh.secret_undefine(cfg.auth_sec_uuid, ignore_status=True)


def _cleanup_files(cfg):
    """ Removes files not needed without ceph disk """

    for f in _FILES_FOR_CLEANUP:
        if os.path.exists(f):
            os.remove(f)
    if (cfg.is_local_img_file
            and cfg.img_file
            and os.path.exists(cfg.img_file)):
        libvirt.delete_local_disk("file", cfg.img_file)


def _update_vm(cfg, vm_name):
    """
    Attaches the new disk to the vm and updates the vm xml

    :param cfg: _Config parameters
    :param vm_name: vm name
    :return: None
    """

    # Disk related config
    disk_src_dict = {"attrs": {"protocol": "rbd",
                               "name": cfg.ceph_disk_name},
                     "hosts": [{"name": cfg.ceph_mon_ip,
                                "port": cfg.ceph_host_port}]}
    # Create disk xml with given config
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    disk_xml = Disk(type_name='network')
    disk_xml.device = cfg.device
    disk_xml.target = {"dev": cfg.device_target, "bus": cfg.device_bus}
    driver_dict = {"name": "qemu", "type": cfg.device_format}
    disk_xml.driver = driver_dict
    disk_source = disk_xml.new_disk_source(**disk_src_dict)
    if cfg.disk_auth_dict:
        logging.debug("disk auth dict is: %s", cfg.disk_auth_dict)
        disk_xml.auth = disk_xml.new_auth(**cfg.disk_auth_dict)
    disk_xml.source = disk_source
    logging.debug("new disk xml is: %s", disk_xml)
    if not cfg.keep_raw_image_as:
        if cfg.hotplug:
            virsh.attach_device(vm_name, disk_xml.xml,
                                flagstr=cfg.attach_option,
                                ignore_status=False, debug=True)
        else:
            vmxml.add_device(disk_xml)
            vmxml.sync()


def _create_image(cfg, vm_name):
    """
    Creates image on ceph

    :param cfg: _Config parameters
    :param vm_name: vm name
    :return:
    """

    # Create necessary image file if not exists
    if cfg.img_file is None:
        cfg.img_file = os.path.join(data_dir.get_data_dir(),
                                    "%s_test.img" % vm_name)
        # Create an local image and make FS on it.
        disk_cmd = ("qemu-img create -f %s %s %s" %
                    (cfg.device_format,
                     cfg.img_file,
                     cfg.storage_size))
        process.run(disk_cmd, ignore_status=False, shell=True)
    # Convert the image to remote ceph storage
    disk_path = ("rbd:%s:mon_host=%s" %
                 (cfg.ceph_disk_name,
                  cfg.ceph_mon_ip))
    if cfg.is_auth_case():
        disk_path += (":id=%s:key=%s" %
                      (cfg.ceph_auth_user, cfg.ceph_auth_key))
    rbd_cmd = ("rbd -m %s %s info %s 2> /dev/null|| qemu-img convert -O"
               " %s %s %s" % (cfg.ceph_mon_ip, cfg.key_opt,
                              cfg.ceph_disk_name, cfg.device_format,
                              cfg.img_file, cfg.disk_path))
    process.run(rbd_cmd, ignore_status=False, shell=True, verbose=True)


def _remove_image(cfg):
    """
    clean up image file if exists

    :param cfg: _Config parameters
    :return: None
    """
    ceph.rbd_image_rm(cfg.ceph_mon_ip,
                      cfg.ceph_disk_name.split('/')[0],
                      cfg.ceph_disk_name.split('/')[1],
                      keyfile=cfg.rbd_key_file)
