#!/usr/bin/python3

import logging
import os
import sys
from keystoneauth1.identity import v3
from keystoneauth1 import session
from .parsecommands import Commands
from .build import BuildFunctions
from .bootstrap import BootstrapFunctions
from .config import Config
from .helpers import Helpers as helpers

class ImageBuilder(object):
    @staticmethod
    def auth(rc):
        auth = v3.Password(auth_url=rc['auth_url'],
                           project_name=rc['project_name'],
                           username=rc['username'],
                           password=rc['password'],
                           user_domain_name=rc['user_domain_name'],
                           project_domain_name=rc['project_domain_name'])
        if rc['cacert'] is not None:
            sess = session.Session(auth,
                                   verify=rc['cacert'])
        else:
            sess = session.Session(auth)
        return sess

    # Only what auth() and the region lookup actually consume. Asking for more
    # than that turns a perfectly good openrc file into a failed login
    REQUIRED_ENV = {
        'username': 'OS_USERNAME',
        'project_name': 'OS_PROJECT_NAME',
        'password': 'OS_PASSWORD',
        'auth_url': 'OS_AUTH_URL',
        'user_domain_name': 'OS_USER_DOMAIN_NAME',
        'project_domain_name': 'OS_PROJECT_DOMAIN_NAME',
        'region_name': 'OS_REGION_NAME',
    }

    @classmethod
    def get_openstack_rc(cls):
        """Reads the credentials from the environment

        OS_CACERT is optional. Raises KeyError naming every variable that is
        missing rather than just the first one found.
        """
        missing = [name for name in cls.REQUIRED_ENV.values()
                   if name not in os.environ]
        if missing:
            raise KeyError(', '.join(missing))
        env_var = {key: os.environ[name]
                   for key, name in cls.REQUIRED_ENV.items()}
        env_var['cacert'] = os.environ.get('OS_CACERT')
        return env_var

def main():
    commands = Commands()
    imagebuilder = ImageBuilder()

    try:
        rc = imagebuilder.get_openstack_rc()
    except KeyError as missing:
        print("""Missing environment variable(s): %s
Please run:
  source <my_openrc>
and try again.""" % missing.args[0], file=sys.stderr)
        sys.exit(1)

    ib_session = imagebuilder.auth(rc)
    region = rc['region_name']

    config = Config().config

    if "IB_TEMPLATE_DIR" in os.environ:
        template_dir = os.environ['IB_TEMPLATE_DIR']
    else:
        try:
            template_dir = config.get('main', 'template_dir')
        except:
            print("Failed to read template_dir from config", file=sys.stderr)
            sys.exit(1)

    if "IB_DOWNLOAD_DIR" in os.environ:
        download_dir = os.environ['IB_DOWNLOAD_DIR']
    else:
        try:
            download_dir = config.get('main', 'download_dir')
        except:
            print("Failed to read download_dir from config", file=sys.stderr)
            sys.exit(1)

    if commands.build_args:
        image_name = commands.build_args.name
        avail_zone = commands.build_args.availability_zone
        flavor = commands.build_args.flavor
        source_image = commands.build_args.source_image
        sshuser = commands.build_args.ssh_username
        provision_script = commands.build_args.provision_script
        network_name = commands.build_args.network_name

        if commands.build_args.verbose:
            logging.basicConfig(format="%(message)s", level=logging.INFO)
        elif commands.build_args.debug:
            logging.basicConfig(level=logging.DEBUG)

        build = BuildFunctions(ib_session,
                               region,
                               image_name,
                               avail_zone,
                               flavor,
                               source_image,
                               sshuser,
                               provision_script,
                               template_dir,
                               download_dir)

        # Everything that can fail without creating anything in OpenStack is
        # done first, so a bad template or network name doesn't leave a
        # security group and a keypair behind
        template_path = build.find_template()
        if not template_path:
            helpers.clean_tmp_files(build.tmp_dir)
            sys.exit(1)

        network_id = build.find_network_id(network_name)

        if not network_id:
            helpers.clean_tmp_files(build.tmp_dir)
            sys.exit(1)

        logging.info('Installing Packer plugins...')
        if build.run_packer_init(template_path) != 0:
            logging.info('Failed to install Packer plugins')
            helpers.clean_tmp_files(build.tmp_dir)
            sys.exit(1)

        logging.info('Creating Packer security group...')
        secgroup_name, secgroup_id = build.create_security_group()

        keypair_id = None
        exitcode = 1

        # From here on something exists in OpenStack, so the rest runs under a
        # finally. An interrupt during the build, or any exception on the way,
        # has to still remove the security group and the keypair rather than
        # leave them behind in the project
        try:
            logging.info('Creating Packer keypair...')
            key_name, keypair_id = build.create_keypairs()
            if key_name is None:
                sys.exit(1)

            logging.info('Running Packer...')
            exitcode = build.run_packer(template_path, secgroup_name, key_name, network_id)
            if exitcode == 0:
                artifact_id = build.parse_manifest()
                if artifact_id is None:
                    exitcode = 1
                else:
                    logging.info("Successfully created image id %s" % artifact_id)
                    if commands.build_args.download:
                        exitcode = build.download_image(artifact_id)
            else:
                logging.info('Build failed')
                exitcode = 1

            if commands.build_args.purge_source:
                if build.delete_image(source_image):
                    logging.info('Successfully deleted source image')
                else:
                    logging.info('Failed to delete source image')
                    exitcode = 1
        finally:
            logging.info('Cleaning up...')
            build.cleanup(secgroup_id, keypair_id)
            helpers.clean_tmp_files(build.tmp_dir)

        sys.exit(exitcode)

    if commands.bootstrap_args:
        image_name = commands.bootstrap_args.name
        avail_zone = commands.bootstrap_args.availability_zone
        url = commands.bootstrap_args.url
        checksum_url = commands.bootstrap_args.checksum_url
        checksum_digest = commands.bootstrap_args.checksum_digest
        disk_format = commands.bootstrap_args.disk_format
        min_disk = commands.bootstrap_args.min_disk
        min_ram = commands.bootstrap_args.min_ram

        if commands.bootstrap_args.verbose:
            logging.basicConfig(format="%(message)s", level=logging.INFO)
        elif commands.bootstrap_args.debug:
            logging.basicConfig(level=logging.DEBUG)

        properties = {}

        if not commands.bootstrap_args.no_scsi_mode:
            properties['hw_disk_bus'] = 'scsi'
            properties['hw_scsi_model'] = 'virtio-scsi'

        if commands.bootstrap_args.efi:
            properties['hw_firmware_type'] = 'uefi'
            properties['hw_machine_type'] = 'q35'

        properties['hw_rng_model'] = 'virtio'

        bootstrap = BootstrapFunctions(ib_session,
                                       region,
                                       avail_zone)
        logging.info('Downloading image...')
        image_file = bootstrap.download_and_check(url, checksum_url, checksum_digest)

        if image_file:
            logging.info('Uploading image to Glance...')
            image_id = bootstrap.create_glance_image(image_file,
                                                     image_name,
                                                     disk_format,
                                                     min_disk,
                                                     min_ram,
                                                     properties)
        else:
            logging.info('Downloading failed.')
            logging.info('Cleaning up...')
            helpers.clean_tmp_files(bootstrap.tmp_dir)
            sys.exit(1)

        if image_id:
            sys.stdout.write(image_id)
        else:
            logging.info('Uploading failed.')
            logging.info('Cleaning up...')
            helpers.clean_tmp_files(bootstrap.tmp_dir)
            sys.exit(1)

        logging.info('Cleaning up...')
        helpers.clean_tmp_files(bootstrap.tmp_dir)
        sys.exit(0)

# vim: set ft=python3
