"""
High-level libvirt ceph utility functions.

This module is meant to reduce code size by performing common ceph setup procedures.
:copyright: 2021 Red Hat Inc.
"""

import os
import logging

from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest import utils_package
from virttest import ceph

from virttest.utils_test import libvirt

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk


def create_ceph_backend_vm_disk(vm, params, is_setup=True):
    """
    Setup vm ceph disk with given parameters

    :param vm: the vm object
    :param params: dict, dict include setup vm disk xml configurations
    :param is_setup: one parameter indicate whether setup or clean up
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    logging.debug("original xml is: %s", vmxml.xmltreefile)

    # Device related configurations
    device_format = params.get("virt_disk_device_format", "raw")
    device_bus = params.get("virt_disk_device_bus", "virtio")
    device = params.get("virt_disk_device", "disk")
    device_target = params.get("virt_disk_device_target", "vdb")
    hotplug = "yes" == params.get("virt_disk_device_hotplug", "no")

    # Ceph related configurations
    ceph_host_ip = params.get("ceph_host_ip", "EXAMPLE_HOSTS")
    ceph_mon_ip = params.get("ceph_mon_ip", "EXAMPLE_MON_HOST")
    ceph_host_port = params.get("ceph_host_port", "EXAMPLE_PORTS")
    ceph_disk_name = params.get("ceph_disk_name", "EXAMPLE_SOURCE_NAME")
    ceph_client_name = params.get("ceph_client_name")
    ceph_client_key = params.get("ceph_client_key")
    ceph_auth_user = params.get("ceph_auth_user")
    ceph_auth_key = params.get("ceph_auth_key")
    auth_sec_usage_type = params.get("ceph_auth_sec_usage_type", "ceph")
    storage_size = params.get("storage_size", "1G")
    img_file = params.get("ceph_image_file")
    key_file = os.path.join(data_dir.get_tmp_dir(), "ceph.key")
    key_opt = ""
    is_local_img_file = False

    # Prepare a blank params to confirm if delete the configure at the end of the test
    ceph_cfg = ""
    disk_auth_dict = None
    if not utils_package.package_install(["ceph-common"]):
        test.error("Failed to install ceph-common")

    # Create config file if it doesn't exist
    ceph_cfg = ceph.create_config_file(ceph_mon_ip)
    # If enable auth, prepare a local file to save key
    if ceph_client_name and ceph_client_key:
        with open(key_file, 'w') as f:
            f.write("[%s]\n\tkey = %s\n" %
                    (ceph_client_name, ceph_client_key))
        key_opt = "--keyring %s" % key_file
    if is_setup:
        # If enable auth, prepare device source
        if ceph_client_name and ceph_client_key:
            auth_sec_dict = {"sec_usage": auth_sec_usage_type,
                             "sec_name": "ceph_auth_secret"}
            auth_sec_uuid = libvirt.create_secret(auth_sec_dict)
            virsh.secret_set_value(auth_sec_uuid, ceph_auth_key,
                                   debug=True)
            disk_auth_dict = {"auth_user": ceph_auth_user,
                              "secret_typ": auth_sec_usage_type,
                              "secret_uuid": auth_sec_uuid}
            device_source = "rbd:%s:mon_host=%s:keyring=%s" % (ceph_disk_name,
                                                               ceph_mon_ip,
                                                               key_file)
        else:
            device_source = "rbd:%s:mon_host=%s" % (ceph_disk_name, ceph_mon_ip)
        logging.debug("device source is: %s", device_source)
        # clean up image file if exists
        logging.debug("pre clean up rbd disk if exists: %s", cmd_result)
        cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
               "{2}".format(ceph_mon_ip, key_opt, ceph_disk_name))
        process.run(cmd, ignore_status=True, shell=True)

        #Create necessary image file if not exists
        if img_file is None:
            img_file = os.path.join(data_dir.get_tmp_dir(),
                                    "%s_test.img" % vm_name)
            # Create an local image and make FS on it.
            disk_cmd = ("qemu-img create -f %s %s %s" %
                        (device_format, img_file, storage_size))
            process.run(disk_cmd, ignore_status=False, shell=True)
            is_local_img_file = True
        # Convert the image to remote ceph storage
        disk_path = ("rbd:%s:mon_host=%s" %
                     (ceph_disk_name, ceph_mon_ip))
        if ceph_client_name and ceph_client_key:
            disk_path += (":id=%s:key=%s" %
                          (ceph_auth_user, ceph_auth_key))
        rbd_cmd = ("rbd -m %s %s info %s 2> /dev/null|| qemu-img convert -O"
                   " %s %s %s" % (ceph_mon_ip, key_opt, ceph_disk_name,
                                  device_format, img_file, disk_path))
        process.run(rbd_cmd, ignore_status=False, shell=True)

        # Disk related config
        disk_src_dict = {"attrs": {"protocol": "rbd",
                                   "name": ceph_disk_name},
                         "hosts":  [{"name": ceph_host_ip,
                                     "port": ceph_host_port}]}
        # Create disk xml with given config
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        disk_xml = Disk(type_name='network')
        disk_xml.device = device
        disk_xml.target = {"dev": device_target, "bus": device_bus}
        driver_dict = {"name": "qemu", "type": device_format}
        disk_xml.driver = driver_dict
        disk_source = disk_xml.new_disk_source(**disk_src_dict)
        if disk_auth_dict:
            logging.debug("disk auth dict is: %s" % disk_auth_dict)
            disk_xml.auth = disk_xml.new_auth(**disk_auth_dict)
        disk_xml.source = disk_source
        logging.debug("new disk xml is: %s", disk_xml)
        if hotplug:
            attach_option = params.get("virt_device_attach_option", "--config")
            virsh.attach_device(vm_name, disk_xml.xml,
                                flagstr=attach_option, ignore_status=False, debug=True)
        else:
            vmxml.add_device(disk_xml)
            vmxml.sync()
    else:
        cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
               "{2}".format(ceph_mon_ip, key_opt, ceph_disk_name))
        cmd_result = process.run(cmd, ignore_status=True, shell=True)
        logging.debug("result of rbd removal: %s", cmd_result)
        # Remove ceph configure file if created.
        if ceph_cfg:
            os.remove(ceph_cfg)
        # Remove ceph key file if has.
        if os.path.exists(key_file):
            os.remove(key_file)o
        if is_local_img_file and os.path.exists(img_file):
            libvirt.delete_local_disk("file", img_file)
