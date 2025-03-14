# Authors:
#     Endi S. Dewata <edewata@redhat.com>
#     Dinesh Prasanth M K <dmoluguw@redhat.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright (C) 2018 Red Hat, Inc.
# All rights reserved.
#

from __future__ import absolute_import

import datetime
import functools
import json
import logging
import os
import pwd
import re
import requests
import shutil
import socket
import subprocess
import tempfile
import time

import ldap
import ldap.filter

import pki
import pki.nssdb
import pki.util
import pki.server
import pki.system

SELFTEST_CRITICAL = 'critical'

logger = logging.getLogger(__name__)


@functools.total_ordering
class PKISubsystem(object):

    def __init__(self, instance, subsystem_name):

        self.instance = instance
        self.name = subsystem_name  # e.g. ca, kra

        self.cs_conf = os.path.join(self.conf_dir, 'CS.cfg')
        self.registry_conf = os.path.join(self.conf_dir, 'registry.cfg')

        self.config = {}
        self.registry = {}

        self.type = subsystem_name.upper()  # e.g. CA, KRA

        self.default_doc_base = os.path.join(
            pki.SHARE_DIR,
            self.name,
            'webapps',
            self.name)

        self.doc_base = os.path.join(instance.webapps_dir, self.name)

        self.default_context_xml = os.path.join(
            pki.SHARE_DIR,
            self.name,
            'conf',
            'Catalina',
            'localhost',
            self.name + '.xml')

        self.context_xml = os.path.join(
            instance.conf_dir,
            'Catalina',
            'localhost',
            self.name + '.xml')

    def __eq__(self, other):
        if not isinstance(other, PKISubsystem):
            return NotImplemented
        return (self.name == other.name and
                self.instance == other.instance and
                self.type == other.type)

    def __ne__(self, other):
        if not isinstance(other, PKISubsystem):
            return NotImplemented
        return not self.__eq__(other)

    def __lt__(self, other):
        if not isinstance(other, PKISubsystem):
            return NotImplemented
        self_type = self.type if self.type is not None else ''
        other_type = other.type if other.type is not None else ''
        return (self.name < other.name or
                self.instance < other.instance or
                self_type < other_type)

    def __hash__(self):
        return hash((self.name, self.instance, self.type))

    @property
    def base_dir(self):
        if hasattr(self.instance, 'version') and self.instance.version < 10:
            return self.instance.base_dir
        return os.path.join(self.instance.base_dir, self.name)

    @property
    def conf_dir(self):
        return os.path.join(self.instance.conf_dir, self.name)

    @property
    def log_dir(self):
        return os.path.join(self.instance.log_dir, self.name)

    @property
    def log_archive_dir(self):
        return os.path.join(self.log_dir, 'archive')

    @property
    def log_signed_audit_dir(self):
        return os.path.join(self.log_dir, 'signedAudit')

    @property
    def registry_dir(self):
        return os.path.join(self.instance.registry_dir, self.name)

    @property
    def default_cfg(self):
        return os.path.join(self.registry_dir, 'default.cfg')

    def create(self, exist_ok=False):

        # Create /var/lib/pki/<instance>/<subsystem>
        self.instance.makedirs(self.base_dir, exist_ok=exist_ok)

    def create_conf(self, exist_ok=False):

        # Create /etc/pki/<instance>/<subsystem>
        self.instance.makedirs(self.conf_dir, exist_ok=exist_ok)

        self.config['cs.type'] = self.type
        self.config['instanceId'] = self.instance.name
        self.config['passwordClass'] = 'com.netscape.cmsutil.password.PlainPasswordFile'
        self.config['passwordFile'] = self.instance.password_conf

        logger.info('Storing subsystem config: %s', self.cs_conf)
        self.instance.store_properties(self.cs_conf, self.config)

        # Copy /usr/share/pki/<subsystem>/conf/registry.cfg
        # to /etc/pki/<instance>/<subsystem>/registry.cfg

        registry_conf = os.path.join(
            pki.server.PKIServer.SHARE_DIR,
            self.name,
            'conf',
            'registry.cfg')

        self.instance.copy(registry_conf, self.registry_conf)

    def create_logs(self, exist_ok=False):

        # Create /var/log/pki/<instance>/<subsystem>
        self.instance.makedirs(self.log_dir, exist_ok=exist_ok)

        # Create /var/log/pki/<instance>/<subsystem>/archive
        self.instance.makedirs(self.log_archive_dir, exist_ok=exist_ok)

        # Create /var/log/pki/<instance>/<subsystem>/signedAudit
        self.instance.makedirs(self.log_signed_audit_dir, exist_ok=exist_ok)

    def create_registry(self, exist_ok=False):

        # Create subsystem registry folder at
        # /etc/sysconfig/pki/tomcat/<instance>/<subsystem>

        self.instance.makedirs(self.registry_dir, exist_ok=exist_ok)

        # Copy /usr/share/pki/server/etc/default.cfg
        # to /etc/sysconfig/pki/tomcat/<instance>/<subsystem>/default.cfg

        default_cfg = os.path.join(
            pki.server.PKIServer.SHARE_DIR,
            'server',
            'etc',
            'default.cfg')

        self.instance.copy(default_cfg, self.default_cfg)

    def load(self):

        self.config.clear()

        if os.path.exists(self.cs_conf):
            logger.info('Loading subsystem config: %s', self.cs_conf)
            pki.util.load_properties(self.cs_conf, self.config)

            self.type = self.config['cs.type']

        self.registry.clear()

        if os.path.exists(self.registry_conf):
            logger.info('Loading subsystem registry: %s', self.registry_conf)
            pki.util.load_properties(self.registry_conf, self.registry)

    def remove_registry(self, force=False):

        # Remove /etc/sysconfig/pki/tomcat/<instance>/<subsystem>/default.cfg

        default_cfg = os.path.join(self.registry_dir, 'default.cfg')
        logger.info('Removing %s', default_cfg)
        pki.util.remove(default_cfg, force=force)

        # Remove subsystem registry folder at
        # /etc/sysconfig/pki/tomcat/<instance>/<subsystem>

        logger.info('Removing %s', self.registry_dir)
        pki.util.rmtree(self.registry_dir, force=force)

    def remove_logs(self, force=False):

        # Remove /var/log/pki/<instance>/<subsystem>/signedAudit
        logger.info('Removing %s', self.log_signed_audit_dir)
        pki.util.rmtree(self.log_signed_audit_dir, force=force)

        # Remove /var/log/pki/<instance>/<subsystem>/archive
        logger.info('Removing %s', self.log_archive_dir)
        pki.util.rmtree(self.log_archive_dir, force=force)

        # Remove /var/log/pki/<instance>/<subsystem>
        logger.info('Removing %s', self.log_dir)
        pki.util.rmtree(self.log_dir, force=force)

    def remove_conf(self, force=False):

        # Remove /etc/pki/<instance>/<subsystem>
        logger.info('Removing %s', self.conf_dir)
        pki.util.rmtree(self.conf_dir, force=force)

    def remove(self, force=False):

        # Remove /var/lib/pki/<instance>/<subsystem>
        logger.info('Removing %s', self.base_dir)
        pki.util.rmtree(self.base_dir, force=force)

    def get_subsystem_index(self, subsystem_id):
        '''
        Get index of subsystem in CS.cfg.
        '''

        # find subsystem.<index>.id params
        pattern = re.compile(r'^subsystem\.(.*)\.id$')

        for key, value in self.config.items():

            m = pattern.match(key)
            if not m:
                continue

            value = self.config[key]
            if value != subsystem_id:
                continue

            # param value matches subsystem ID -> return index
            index = m.group(1)
            return int(index)

        return None

    def find_system_certs(self):

        cert_ids = self.config['%s.cert.list' % self.name].split(',')

        for cert_id in cert_ids:
            yield self.get_subsystem_cert(cert_id)

    def get_cert_infos(self):

        cert_ids = self.config['%s.cert.list' % self.name].split(',')

        certs = []

        for cert_id in cert_ids:
            cert = self.get_cert_info(cert_id)
            certs.append(cert)

        return certs

    def get_subsystem_cert(self, tag):

        logger.debug('PKISubsystem.get_subsystem_cert(%s)', tag)

        cert = self.get_cert_info(tag)

        if not cert['nickname']:
            return cert

        # get cert info from NSS database
        cert_info = self.get_nssdb_cert_info(tag)

        if cert_info:
            cert.update(cert_info)

        return cert

    def get_cert_info(self, tag):

        logger.info('Getting %s cert info from CS.cfg', tag)

        cert = {}
        cert['id'] = tag
        cert['nickname'] = self.config.get('%s.%s.nickname' % (self.name, tag))
        cert['token'] = self.config.get('%s.%s.tokenname' % (self.name, tag))
        cert['certusage'] = self.config.get('%s.cert.%s.certusage' % (self.name, tag))

        return cert

    def get_nssdb_cert_info(self, tag):

        logger.debug('PKISubsystem.get_nssdb_cert_info(%s)', tag)
        logger.info('Getting %s cert info from NSS database', tag)

        nickname = self.config.get('%s.%s.nickname' % (self.name, tag))
        token = self.config.get('%s.%s.tokenname' % (self.name, tag))

        nssdb = self.instance.open_nssdb()
        try:
            return nssdb.get_cert_info(nickname, token=token)
        finally:
            nssdb.close()

    def update_system_cert(self, cert):
        cert_id = cert['id']
        self.config['%s.%s.nickname' % (self.name, cert_id)] = cert.get('nickname')
        self.config['%s.%s.tokenname' % (self.name, cert_id)] = cert.get('token')
        certs_path = os.path.join(self.instance.conf_dir, 'certs')
        self.instance.makedirs(certs_path, exist_ok=True)
        if cert_id != 'sslserver' and cert_id != 'subsystem':
            cert_id = self.name + '_' + cert_id
        csr_file = os.path.join(certs_path, cert_id + '.csr')
        with open(csr_file, "w", encoding='utf-8') as f:
            f.write(pki.nssdb.convert_csr(cert.get('request'), 'base64', 'pem'))
        os.chown(csr_file, self.instance.uid, self.instance.gid)

    def validate_system_cert(self, tag):

        logger.info('Validate %s cert', tag)

        cert = self.get_subsystem_cert(tag)

        nickname = cert['nickname']
        token = pki.nssdb.normalize_token(cert['token'])

        if token:
            fullname = token + ':' + nickname
        else:
            fullname = nickname

        cert_usage = cert['certusage']

        cmd = [
            'pki',
            '-d', self.instance.nssdb_dir,
            '-f', self.instance.password_conf
        ]

        if token:
            cmd.extend(['--token', token])

        cmd.extend([
            'client-cert-validate',
            '--certusage', cert_usage,
            fullname
        ])

        logger.debug('Command: %s', ' '.join(cmd))

        # don't use capture_output and text params to support Python 3.6
        # https://stackoverflow.com/questions/53209127/subprocess-unexpected-keyword-argument-capture-output/53209196
        # https://stackoverflow.com/questions/52663518/python-subprocess-popen-doesnt-take-text-argument

        try:
            subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                universal_newlines=True)

            logger.debug('%s certificate is valid', tag)

        except subprocess.CalledProcessError as e:
            logger.error('Unable to validate %s certificate: %s', tag, e.stdout)
            raise

    def export_system_cert(
            self,
            cert_id,
            pkcs12_file,
            pkcs12_password_file,
            no_key=False,
            append=False):

        cert = self.get_subsystem_cert(cert_id)
        nickname = cert['nickname']
        token = pki.nssdb.normalize_token(cert['token'])

        if token:
            nickname = token + ':' + nickname

        tmpdir = tempfile.mkdtemp()

        try:
            # add the certificate, key, and chain
            cmd = [
                'pki',
                '-d', self.instance.nssdb_dir,
                '-f', self.instance.password_conf
            ]

            cmd.extend([
                'pkcs12-cert-import',
                '--pkcs12-file', pkcs12_file,
                '--pkcs12-password-file', pkcs12_password_file,
            ])

            if no_key:
                cmd.extend(['--no-key'])

            if append:
                cmd.extend(['--append'])

            if logger.isEnabledFor(logging.DEBUG):
                cmd.append('--debug')

            elif logger.isEnabledFor(logging.INFO):
                cmd.append('-v')

            cmd.extend([
                nickname
            ])

            logger.debug('Command: %s', ' '.join(cmd))

            subprocess.check_call(cmd)

        finally:
            shutil.rmtree(tmpdir)

    def export_cert_chain(
            self,
            pkcs12_file,
            pkcs12_password_file):

        # use subsystem certificate to get certificate chain
        cert = self.get_subsystem_cert('subsystem')
        nickname = cert['nickname']
        token = pki.nssdb.normalize_token(cert['token'])

        nssdb_password = self.instance.get_token_password(token)

        tmpdir = tempfile.mkdtemp()

        try:
            nssdb_password_file = os.path.join(tmpdir, 'password.txt')
            with open(nssdb_password_file, 'w', encoding='utf-8') as f:
                f.write(nssdb_password)

            # export the certificate, key, and chain
            cmd = [
                'pki',
                '-d', self.instance.nssdb_dir,
                '-C', nssdb_password_file
            ]

            if token:
                cmd.extend(['--token', token])

            cmd.extend([
                'pkcs12-export',
                '--pkcs12', pkcs12_file,
                '--password-file', pkcs12_password_file,
                nickname
            ])

            logger.debug('Command: %s', ' '.join(cmd))

            subprocess.check_call(cmd)

            # remove the certificate and key, but keep the chain
            cmd = [
                'pki',
                '-d', self.instance.nssdb_dir,
                '-C', nssdb_password_file
            ]

            if token:
                cmd.extend(['--token', token])

            cmd.extend([
                'pkcs12-cert-del',
                '--pkcs12-file', pkcs12_file,
                '--pkcs12-password-file', pkcs12_password_file,
                nickname
            ])

            logger.debug('Command: %s', ' '.join(cmd))

            subprocess.check_call(cmd)

        finally:
            shutil.rmtree(tmpdir)

    def save(self):

        logger.info('Storing subsystem config: %s', self.cs_conf)
        self.instance.store_properties(self.cs_conf, self.config)

        logger.info('Storing registry config: %s', self.registry_conf)
        self.instance.store_properties(self.registry_conf, self.registry)

    def is_valid(self):
        return os.path.exists(self.conf_dir)

    def validate(self):
        if not self.is_valid():
            raise pki.PKIException(
                'Invalid subsystem: ' + self.__repr__(),
                None, self.instance)

    def is_enabled(self):
        return self.instance.is_deployed(self.name)

    def is_ready(self, secure_connection=True, timeout=None):

        server_config = self.instance.get_server_config()

        if secure_connection:
            protocol = 'https'
            port = server_config.get_secure_port()

        else:
            protocol = 'http'
            port = server_config.get_unsecure_port()

        # When waiting for a connection to come alive, don't bother verifying
        # the certificate at this stage.
        connection = pki.client.PKIConnection(
            protocol=protocol,
            hostname=socket.getfqdn(),
            port=port,
            accept='application/json',
            trust_env=False,
            verify=False)

        client = pki.system.SystemStatusClient(connection, subsystem=self.name)
        response = client.get_status(timeout=timeout)
        json_response = json.loads(response)
        status = json_response['Response']['Status']

        logger.info('Subsystem status: %s', status)
        return status == 'running'

    def wait_for_startup(self, startup_timeout=None, request_timeout=None):
        """
        Wait for subsystem to become ready to serve requests.

        :param startup_timeout: Total timeout. Unsuccessful status requests will
            be retried until this timeout is exceeded. Default: None.
        :param request_timeout: Connect/receive timeout for each individual
            status request. Default: None.
        """

        fips_mode = pki.FIPS.is_enabled()

        # must use 'http' protocol when FIPS mode is enabled
        secure_connection = not fips_mode

        start_time = datetime.datetime.today()
        ready = False
        counter = 0

        while not ready:
            try:
                time.sleep(1)

                ready = self.is_ready(
                    secure_connection=secure_connection,
                    timeout=request_timeout)

            except requests.exceptions.SSLError as exc:
                max_retry_error = exc.args[0]
                reason = getattr(max_retry_error, 'reason')
                raise Exception('Server unreachable due to SSL error: %s' % reason) from exc

            except pki.RETRYABLE_EXCEPTIONS as exc:

                stop_time = datetime.datetime.today()
                counter = (stop_time - start_time).total_seconds()

                if startup_timeout is not None and counter >= startup_timeout:
                    raise Exception('%s subsystem did not start after %ds' %
                                    (self.type, startup_timeout)) from exc

                logger.info(
                    'Waiting for %s subsystem to start (%ds)',
                    self.type,
                    int(round(counter)))

    def enable(self, wait=False, max_wait=60, timeout=None):

        if os.path.exists(self.doc_base):
            # deploy custom subsystem if exists
            doc_base = self.doc_base

        else:
            # otherwise deploy default subsystem directly from
            # /usr/share/pki/<subsystem>/webapps/<subsystem>
            doc_base = None

        self.instance.deploy_webapp(
            self.name,
            self.default_context_xml,
            doc_base=doc_base,
            wait=wait,
            max_wait=max_wait,
            timeout=timeout)

    def disable(self, force=False, wait=False, max_wait=60, timeout=None):

        self.instance.undeploy_webapp(
            self.name,
            force=force,
            wait=wait,
            max_wait=max_wait,
            timeout=timeout)

    def restart(self, wait=False, max_wait=60, timeout=None):
        self.disable(wait=True, max_wait=max_wait, timeout=timeout)
        self.enable(wait=wait, max_wait=max_wait, timeout=timeout)

    def open_database(self, name='internaldb', bind_dn=None,
                      bind_password=None):

        # TODO: add LDAPI support
        hostname = self.config['%s.ldapconn.host' % name]
        port = self.config['%s.ldapconn.port' % name]
        secure = self.config['%s.ldapconn.secureConn' % name]

        if secure == 'true':
            url = 'ldaps://%s:%s' % (hostname, port)

        elif secure == 'false':
            url = 'ldap://%s:%s' % (hostname, port)

        else:
            raise Exception(
                'Invalid parameter value in %s.ldapconn.secureConn: %s' %
                (name, secure))

        connection = pki.server.PKIDatabaseConnection(url)

        connection.set_security_database(self.instance.nssdb_dir)

        auth_type = self.config['%s.ldapauth.authtype' % name]
        if (bind_dn is not None and bind_password is not None):
            # connect using the provided credentials
            connection.set_credentials(
                bind_dn=bind_dn,
                bind_password=bind_password
            )
        elif auth_type == 'BasicAuth':
            connection.set_credentials(
                bind_dn=self.config['%s.ldapauth.bindDN' % name],
                bind_password=self.instance.get_password(name)
            )

        elif auth_type == 'SslClientAuth':
            connection.set_credentials(
                client_cert_nickname=self.config[
                    '%s.ldapauth.clientCertNickname' % name],
                # TODO: remove hard-coded token name
                nssdb_password=self.instance.get_token_password(
                    pki.nssdb.INTERNAL_TOKEN_NAME)
            )

        else:
            raise Exception(
                'Invalid parameter value in %s.ldapauth.authtype: %s' %
                (name, auth_type))

        connection.open()

        return connection

    def customize_file(self, input_file, output_file):
        params = {
            '{instanceId}': self.instance.name,
            '{database}': self.config['internaldb.database'],
            '{rootSuffix}': self.config['internaldb.basedn']
        }

        pki.util.customize_file(input_file, output_file, params)

    def enable_audit_event(self, event_name):

        if not event_name:
            raise ValueError("Please specify the Event name")

        if event_name not in self.get_audit_events():
            raise pki.server.PKIServerException('Invalid audit event: %s' % event_name)

        value = self.config['log.instance.SignedAudit.events']
        events = set(value.replace(' ', '').split(','))

        if event_name in events:
            return False

        events.add(event_name)
        event_list = ','.join(sorted(events))
        self.config['log.instance.SignedAudit.events'] = event_list

        return True

    def update_audit_event_filter(self, event_name, event_filter):

        if not event_name:
            raise ValueError("Please specify the Event name")

        if event_name not in self.get_audit_events():
            raise pki.server.PKIServerException('Invalid audit event: %s' % event_name)

        name = 'log.instance.SignedAudit.filters.%s' % event_name

        if event_filter:
            self.config[name] = event_filter
        else:
            self.config.pop(name, None)

    def disable_audit_event(self, event_name):

        if not event_name:
            raise ValueError("Please specify the Event name")

        if event_name not in self.get_audit_events():
            raise pki.server.PKIServerException('Invalid audit event: %s' % event_name)

        value = self.config['log.instance.SignedAudit.events']
        events = set(value.replace(' ', '').split(','))

        if event_name not in events:
            return False

        events.remove(event_name)
        event_list = ','.join(sorted(events))
        self.config['log.instance.SignedAudit.events'] = event_list

        return True

    def find_audit_event_configs(self, enabled=None, enabled_by_default=None):
        '''
        This method returns current audit configuration based on the specified
        filters.
        '''

        events = self.get_audit_events()
        enabled_events = set(self.get_enabled_audit_events())

        # apply "enabled_by_default" filter
        if enabled_by_default is None:
            # return all events
            names = set(events.keys())

        else:
            # return events enabled by default
            names = set()
            for name, event in events.items():
                if enabled_by_default is event['enabled_by_default']:
                    names.add(name)

        # apply "enabled" filter
        if enabled is None:
            # return all events
            pass

        elif enabled:  # enabled == True
            # return currently enabled events
            names = names.intersection(enabled_events)

        else:  # enabled == False
            # return currently disabled events
            names = names.difference(enabled_events)

        results = []

        # get event properties
        for name in sorted(names):
            event = {}
            event['name'] = name
            event['enabled'] = name in enabled_events
            event['filter'] = self.config.get('log.instance.SignedAudit.filters.%s' % name)
            results.append(event)

        return results

    def get_audit_event_config(self, name):

        if name not in self.get_audit_events():
            raise pki.server.PKIServerException('Invalid audit event: %s' % name)

        enabled_event_names = self.get_enabled_audit_events()

        event = {}
        event['name'] = name
        event['enabled'] = name in enabled_event_names
        event['filter'] = self.config.get('log.instance.SignedAudit.filters.%s' % name)

        return event

    def get_audit_events(self):
        '''
        This method returns audit events applicable to this subsystem
        as a map of objects.
        '''

        # get the list of audit events from audit-events.properties

        tmpdir = tempfile.mkdtemp()

        try:
            # export audit-events.properties from pki-server.jar
            server_jar = \
                '/usr/share/pki/%s/webapps/%s/WEB-INF/lib/pki-server.jar' \
                % (self.name, self.name)

            cmd = [
                'jar',
                'xf',
                server_jar,
                'audit-events.properties'
            ]

            logger.debug('Command: %s', ' '.join(cmd))

            subprocess.check_output(
                cmd,
                cwd=tmpdir,
                stderr=subprocess.STDOUT)

            # load audit-events.properties
            filename = os.path.join(tmpdir, 'audit-events.properties')
            events = pki.server.PKIServer.load_audit_events(filename)

        finally:
            shutil.rmtree(tmpdir)

        # get audit events for this subsystem
        results = {}
        subsystem = self.name.upper()

        for name, event in events.items():
            if subsystem in event['subsystems']:
                logger.info('Returning %s', name)
                results[name] = event

        return results

    def get_enabled_audit_events(self):

        # parse enabled audit events
        value = self.config['log.instance.SignedAudit.events']
        events = set(value.replace(' ', '').split(','))

        return sorted(events)

    def get_audit_log_dir(self):

        current_file_path = self.config['log.instance.SignedAudit.fileName']
        return os.path.dirname(current_file_path)

    def get_audit_log_files(self):

        current_file_path = self.config['log.instance.SignedAudit.fileName']
        (log_dir, current_file) = os.path.split(current_file_path)

        # sort log files based on timestamp
        files = [f for f in os.listdir(log_dir) if f != current_file]
        files.sort()

        # put the current log file at the end
        files.append(current_file)

        return files

    def __repr__(self):
        return str(self.instance) + '/' + self.name

    def get_startup_tests(self):
        # Split the line 'selftest.container.selftests.startup'
        v = self.config.get('selftests.container.order.startup', '').strip()
        if len(v) == 0:
            # special case; empty value -> empty list
            available_tests = []
        else:
            available_tests = v.split(',')

        target_tests = {}
        for testInfo in available_tests:
            temp = testInfo.split(':')
            test_name = temp[0].strip()

            target_tests[test_name] = False
            # Check if there is some test level mentioned after colon
            if len(temp) > 1:
                # Check if the test is critical
                target_tests[test_name] = temp[1].strip() == SELFTEST_CRITICAL

        return target_tests

    def set_startup_tests(self, target_tests):
        # Remove unnecessary space, curly braces
        self.config['selftests.container.order.startup'] = ", " \
            .join([(key + ':' + SELFTEST_CRITICAL if val else key)
                   for key, val in target_tests.items()])

    def set_startup_test_criticality(self, critical, test=None):
        # Assume action to be taken on ALL available startup tests
        target_tests = self.get_startup_tests()

        # If just one test is provided, take action on ONLY that test
        if test:
            if test not in target_tests:
                raise pki.server.PKIServerException(
                    'No such self test available for %s' % self.name)
            target_tests[test] = critical
        else:
            for testID in target_tests.keys():
                target_tests[testID] = critical
        self.set_startup_tests(target_tests)

    def get_cert_ski(self, cert_data):
        """
        Get the Subject Key Identifier of a certificate

        :param cert_data: Base64-encoded cert data
        :type cert_data: str
        :return: ski
        """

        pem_cert = pki.nssdb.convert_cert(cert_data, 'base64', 'pem')

        tmpdir = tempfile.mkdtemp()
        try:
            cert_file = os.path.join(tmpdir, 'cert.crt')
            with open(cert_file, 'w', encoding='utf-8') as f:
                f.write(pem_cert)

            cmd = [
                'openssl',
                'x509',
                '-in', cert_file,
                '-noout',
                '-text'
            ]

            logger.debug('Command: %s', ' '.join(cmd))
            cert_info = subprocess.check_output(cmd).decode('utf-8')

        finally:
            shutil.rmtree(tmpdir)

        ski = re.search(r'Subject Key Identifier.*\n.*?(.*?)\n', cert_info).group(1)

        ski = '0x' + ski.strip().replace(':', '')
        logger.info('SKI: %s', ski)

        return ski

    def temp_cert_create(self, nssdb, cert_tag, serial, new_cert_file):
        """
        Generates temp cert with validity of 3 months by default

        **Note**: Currently, supports only *sslserver* cert

        :param nssdb: NSS db instance
        :type nssdb: NSSDatabase
        :param cert_tag: Cert for which temp cert needs to be created
        :type cert_tag: str
        :param serial: Serial number to be assigned to new cert
        :type serial: str
        :param new_cert_file: Path where the new temp cert needs to be written to
        :type new_cert_file: str
        :return: None
        :rtype: None
        """
        logger.info('Generate temp SSL certificate')

        if cert_tag != 'sslserver':
            raise pki.server.PKIServerException(
                'Temp cert for %s is not supported yet.' % cert_tag)

        ca_signing_cert = self.instance.get_subsystem('ca').get_subsystem_cert('signing')
        # TODO: Support remote CA.

        ca_cert_data = ca_signing_cert.get('data')
        logger.debug('CA signing cert: %s', ca_cert_data)

        if ca_cert_data is None:
            raise pki.server.PKIServerException('Missing CA signing certificate')

        aki = self.get_cert_ski(ca_cert_data)

        nickname = ca_signing_cert['nickname']
        token = ca_signing_cert['token']

        if not pki.nssdb.internal_token(token):
            nickname = token + ':' + nickname

        logger.debug('CA signing cert nickname: %s', nickname)

        csr_file = self.instance.csr_file(cert_tag)
        logger.debug('Reusing existing CSR in %s', csr_file)

        # --keyUsage
        key_usage_ext = {
            'digitalSignature': True,
            'nonRepudiation': True,
            'keyEncipherment': True,
            'dataEncipherment': True,
            'critical': True
        }

        # -3
        aki_ext = {
            'auth_key_id': aki
        }

        # --extKeyUsage
        ext_key_usage_ext = {
            'serverAuth': True
        }

        logger.debug('Creating temp cert')

        rc = nssdb.create_cert(
            issuer=nickname,
            request_file=csr_file,
            cert_file=new_cert_file,
            serial=serial,
            key_usage_ext=key_usage_ext,
            aki_ext=aki_ext,
            ext_key_usage_ext=ext_key_usage_ext,
            use_jss=True)

        if rc:
            raise pki.server.PKIServerException(
                'Failed to generate CA-signed temp SSL certificate. RC: %d' % rc)

    def get_db_config(self):
        """Return DB configuration as dict."""
        shortkeys = [
            'ldapconn.host', 'ldapconn.port', 'ldapconn.secureConn',
            'ldapauth.authtype', 'ldapauth.bindDN', 'ldapauth.bindPWPrompt',
            'ldapauth.clientCertNickname', 'database', 'basedn',
            'multipleSuffix.enable', 'maxConns', 'minConns',
        ]
        db_keys = ['internaldb.{}'.format(x) for x in shortkeys]
        return {k: v for k, v in self.config.items() if k in db_keys}

    def set_db_config(self, new_config):
        """Write the dict of DB configuration to subsystem config.

        Right now this does not perform sanity checks; it just calls
        ``update`` on the config dict.  Fields that are ``None`` will
        overwrite the existing key.  So if you do not want to reset a
        field, ensure the key is absent.

        Likewise, extraneous fields will be set into the main config.

        """
        self.config.update(new_config)

    def import_ldif(self, bind_dn, bind_password, filename):

        # TODO(alee) re-implement this using open_database
        host = self.config['internaldb.ldapconn.host']
        port = self.config['internaldb.ldapconn.port']
        secure = self.config['internaldb.ldapconn.secureConn']

        cmd = [
            'ldapmodify',
            '-c',
            '-D', bind_dn,
            '-w', bind_password,
            '-h', host,
            '-p', port,
            '-f', filename
        ]

        if secure.lower() == 'true':
            cmd.append('-Z')

        logger.debug('Command: %s', ' '.join(cmd))
        subprocess.check_call(cmd)

    def init_database(
            self,
            setup_schema=False,
            create_database=False,
            create_base=False,
            create_containers=False,
            as_current_user=False):

        cmd = [self.name + '-db-init']

        if setup_schema:
            cmd.append('--setup-schema')

        if create_database:
            cmd.append('--create-database')

        if create_base:
            cmd.append('--create-base')

        if create_containers:
            cmd.append('--create-containers')

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def add_indexes(self):

        cmd = [self.name + '-db-index-add']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd)

    def rebuild_indexes(self):

        cmd = [self.name + '-db-index-rebuild']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd)

    def empty_database(self, force=False, as_current_user=False):

        cmd = [self.name + '-db-empty']

        if force:
            cmd.append('--force')

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def remove_database(self, force=False, as_current_user=False):

        cmd = [self.name + '-db-remove']

        if force:
            cmd.append('--force')

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def grant_database_access(
            self,
            dn,
            as_current_user=False):

        cmd = [self.name + '-db-access-grant']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(dn)

        self.run(
            cmd,
            as_current_user=as_current_user,
            capture_output=True)

    def revoke_database_access(
            self,
            dn,
            as_current_user=False):

        cmd = [self.name + '-db-access-revoke']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(dn)

        self.run(
            cmd,
            as_current_user=as_current_user,
            capture_output=True)

    def setup_replication(
            self,
            master_config,
            master_replication_port=None,
            replica_replication_port=None,
            replication_security=None):

        tmpdir = tempfile.mkdtemp()
        try:
            master_config_file = os.path.join(tmpdir, 'master.conf')
            pki.util.store_properties(master_config_file, master_config)

            cmd = [self.name + '-db-replication-setup']

            if master_replication_port:
                cmd.extend(['--master-replication-port', master_replication_port])

            if replica_replication_port:
                cmd.extend(['--replica-replication-port', replica_replication_port])

            if replication_security:
                cmd.extend(['--replication-security', replication_security])

            if master_config_file:
                cmd.extend(['--master-config', master_config_file])

            if logger.isEnabledFor(logging.DEBUG):
                cmd.append('--debug')

            elif logger.isEnabledFor(logging.INFO):
                cmd.append('--verbose')

            # run as current user so it can read the input file
            self.run(cmd, as_current_user=True)

        finally:
            shutil.rmtree(tmpdir)

    def find_vlv(self, as_current_user=False):

        cmd = [self.name + '-db-vlv-find']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def add_vlv(self, as_current_user=False):

        cmd = [self.name + '-db-vlv-add']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def delete_vlv(self, as_current_user=False):

        cmd = [self.name + '-db-vlv-del']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def reindex_vlv(self, as_current_user=False):

        cmd = [self.name + '-db-vlv-reindex']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def request_range(self, master_url, range_type, session_id=None, install_token=None):

        tmpdir = tempfile.mkdtemp()
        try:
            if not install_token:
                install_token = os.path.join(tmpdir, 'install-token')
                with open(install_token, 'w', encoding='utf-8') as f:
                    f.write(session_id)

            cmd = [
                'pki',
                '-d', self.instance.nssdb_dir,
                '-f', self.instance.password_conf,
                '-U', master_url,
                '--ignore-banner',
                '%s-range-request' % self.name,
                range_type,
                '--install-token', install_token,
                '--output-format', 'json'
            ]

            if logger.isEnabledFor(logging.DEBUG):
                cmd.append('--debug')

            elif logger.isEnabledFor(logging.INFO):
                cmd.append('--verbose')

            logger.debug('Command: %s', ' '.join(cmd))
            output = subprocess.check_output(cmd)

            return json.loads(output.decode())

        finally:
            shutil.rmtree(tmpdir)

    def request_ranges(self, master_url, session_id=None, install_token=None):

        # request cert/key request ID range if it uses legacy generator
        if self.type in ['CA', 'KRA'] and \
                self.config.get('dbs.request.id.generator', 'legacy') == 'legacy':

            logger.info('Requesting request ID range')

            request_range = self.request_range(
                master_url, 'request', session_id=session_id, install_token=install_token)

            self.config['dbs.beginRequestNumber'] = request_range['begin']
            self.config['dbs.endRequestNumber'] = request_range['end']

        # request cert/key ID range if it uses legacy generator
        if self.type == 'CA' and \
                self.config.get('dbs.cert.id.generator', 'legacy') == 'legacy' or \
                self.type == 'KRA' \
                and self.config.get('dbs.key.id.generator', 'legacy') == 'legacy':

            logger.info('Requesting serial number range')

            serial_range = self.request_range(
                master_url, 'serialNo', session_id=session_id, install_token=install_token)

            self.config['dbs.beginSerialNumber'] = serial_range['begin']
            self.config['dbs.endSerialNumber'] = serial_range['end']

        # always request replica ID range since it doesn't support random generator
        logger.info('Requesting replica ID range')

        replica_range = self.request_range(
            master_url, 'replicaId', session_id=session_id, install_token=install_token)
        self.config['dbs.beginReplicaNumber'] = replica_range['begin']
        self.config['dbs.endReplicaNumber'] = replica_range['end']

        self.config['dbs.enableSerialManagement'] = 'true'

        self.save()

    def update_ranges(self, as_current_user=False):

        cmd = [self.name + '-range-update']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def retrieve_config(self, master_url, names, substores, session_id=None, install_token=None):

        tmpdir = tempfile.mkdtemp()
        try:
            if not install_token:
                install_token = os.path.join(tmpdir, 'install-token')
                with open(install_token, 'w', encoding='utf-8') as f:
                    f.write(session_id)

            cmd = [
                'pki',
                '-d', self.instance.nssdb_dir,
                '-f', self.instance.password_conf,
                '-U', master_url,
                '--ignore-banner',
                '%s-config-export' % self.name,
                '--names', ','.join(names),
                '--substores', ','.join(substores),
                '--install-token', install_token,
                '--output-format', 'json'
            ]

            if logger.isEnabledFor(logging.DEBUG):
                cmd.append('--debug')

            elif logger.isEnabledFor(logging.INFO):
                cmd.append('--verbose')

            logger.debug('Command: %s', ' '.join(cmd))
            output = subprocess.check_output(cmd)

            return json.loads(output.decode())

        finally:
            shutil.rmtree(tmpdir)

    def import_master_config(self, properties):

        for name in properties:

            if name.startswith('internaldb'):
                # don't import master database configuration
                continue

            elif name.startswith('cloning.ca'):
                new_name = 'preop.ca' + name[10:]

            elif name.startswith('cloning'):
                new_name = 'preop.cert' + name[7:]

            else:
                new_name = name

            value = properties.get(name)
            self.config[new_name] = value

        self.config['preop.clone.configuration'] = 'true'

        self.save()

    def configure_security_domain(
            self,
            hostname,
            port,
            secure_port):

        self.config['securitydomain.host'] = hostname
        self.config['securitydomain.httpport'] = port
        self.config['securitydomain.httpsadminport'] = secure_port

    def create_security_domain(self, name=None, as_current_user=False):

        cmd = [self.name + '-sd-create']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        if name:
            cmd.extend(['--name', name])

        self.run(cmd, as_current_user=as_current_user)

    def display_security_domain_subsystems(self, as_current_user=False):
        '''
        Display security domain subsystems on standard output.

        TODO: Convert this method into find_security_domain_subsystems()
        which returns a JSON object containing the subsystem information.
        '''

        cmd = [self.name + '-sd-subsystem-find']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def add_security_domain_subsystem(
            self,
            subsystem_id,
            subsystem_type,
            hostname,
            unsecure_port=None,
            secure_port='8443',
            domain_manager=False,
            clone=False,
            as_current_user=False):

        cmd = [
            self.name + '-sd-subsystem-add',
            '--subsystem', subsystem_type,
            '--hostname', hostname
        ]

        if unsecure_port:
            cmd.append('--unsecure-port')
            cmd.append(unsecure_port)

        if secure_port:
            cmd.append('--secure-port')
            cmd.append(secure_port)

        if domain_manager:
            cmd.append('--domain-manager')

        if clone:
            cmd.append('--clone')

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(subsystem_id)

        self.run(cmd, as_current_user=as_current_user)

    def remove_security_domain_subsystem(
            self,
            subsystem_id,
            as_current_user=False):

        cmd = [self.name + '-sd-subsystem-del']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(subsystem_id)

        self.run(cmd, as_current_user=as_current_user)

    def join_security_domain(
            self,
            sd_url,
            host_id,
            hostname,
            unsecure_port=None,
            secure_port='8443',
            domain_manager=False,
            clone=False,
            session_id=None,
            install_token=None):

        tmpdir = tempfile.mkdtemp()
        try:
            if not install_token:
                install_token = os.path.join(tmpdir, 'install-token')
                with open(install_token, 'w', encoding='utf-8') as f:
                    f.write(session_id)

            cmd = [
                'pki',
                '-d', self.instance.nssdb_dir,
                '-f', self.instance.password_conf,
                '-U', sd_url,
                '--ignore-banner',
                'securitydomain-join',
                '--install-token', install_token,
                '--type', self.type,
                '--hostname', hostname,
                '--secure-port', secure_port,
            ]

            if unsecure_port is not None:
                cmd.extend(['--unsecure-port', unsecure_port])

            if domain_manager:
                cmd.append('--domain-manager')

            if clone:
                cmd.append('--clone')

            if logger.isEnabledFor(logging.DEBUG):
                cmd.append('--debug')

            elif logger.isEnabledFor(logging.INFO):
                cmd.append('--verbose')

            cmd.append(host_id)

            logger.debug('Command: %s', ' '.join(cmd))
            subprocess.check_call(cmd)

        finally:
            shutil.rmtree(tmpdir)

    def leave_security_domain(
            self,
            sd_url,
            host_id,
            hostname,
            secure_port):

        nickname = self.config.get('%s.cert.subsystem.nickname' % self.name)

        cmd = [
            'pki',
            '-d', self.instance.nssdb_dir,
            '-f', self.instance.password_conf,
            '-n', nickname,
            '-U', sd_url,
            '--ignore-banner',
            'securitydomain-leave',
            '--type', self.type,
            '--hostname', hostname,
            '--secure-port', secure_port
        ]

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(host_id)

        logger.debug('Command: %s', ' '.join(cmd))
        subprocess.check_call(cmd)

    def find_groups(self, member_id=None, as_current_user=False):

        cmd = [self.name + '-group-find']

        if member_id is not None:
            cmd.extend(['--member', member_id])

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def find_group_members(self, group_id, as_current_user=False):

        cmd = [self.name + '-group-member-find']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append('--output-format')
        cmd.append('json')

        cmd.append(group_id)

        result = self.run(
            cmd,
            as_current_user=as_current_user,
            capture_output=True)

        return json.loads(result.stdout.decode())

    def add_group_member(self, group_id, member_id, as_current_user=False):

        cmd = [self.name + '-group-member-add']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(group_id)
        cmd.append(member_id)

        self.run(cmd, as_current_user=as_current_user)

    def remove_group_member(self, group_id, member_id, as_current_user=False):

        cmd = [self.name + '-group-member-del']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(group_id)
        cmd.append(member_id)

        self.run(cmd, as_current_user=as_current_user)

    def find_users(self, see_also=None, as_current_user=False):

        cmd = [self.name + '-user-find']

        if see_also:
            cmd.append('--see-also')
            cmd.append(see_also)

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append('--output-format')
        cmd.append('json')

        result = self.run(
            cmd,
            as_current_user=as_current_user,
            capture_output=True)

        return json.loads(result.stdout.decode())

    def get_user(self, user_id, as_current_user=False):

        cmd = [self.name + '-user-show']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append('--output-format')
        cmd.append('json')

        cmd.append(user_id)

        result = self.run(
            cmd,
            as_current_user=as_current_user,
            capture_output=True)

        return json.loads(result.stdout.decode())

    def add_user(self,
                 user_id,
                 full_name=None,
                 email=None,
                 password=None,
                 password_file=None,
                 phone=None,
                 user_type=None,
                 state=None,
                 tps_profiles=None,
                 as_current_user=False,
                 attributes=None):

        tmpdir = tempfile.mkdtemp()

        try:
            if password and not password_file:
                password_file = os.path.join(tmpdir, 'password.txt')
                with open(password_file, 'w', encoding='utf-8') as f:
                    f.write(password)

            cmd = [self.name + '-user-add']

            if full_name:
                cmd.append('--full-name')
                cmd.append(full_name)

            if email:
                cmd.append('--email')
                cmd.append(email)

            if password_file:
                cmd.append('--password-file')
                cmd.append(password_file)

            if phone:
                cmd.append('--phone')
                cmd.append(phone)

            if user_type:
                cmd.append('--type')
                cmd.append(user_type)

            if state:
                cmd.append('--state')
                cmd.append(state)

            if tps_profiles:
                cmd.append('--tps-profiles')
                cmd.append(','.join(tps_profiles))

            if attributes:
                cmd.append('--attributes')
                attr_str = ''
                for key in attributes:
                    attr_str += key + ':' + attributes[key] + ','
                attr_str = attr_str.strip(',')
                cmd.append(attr_str)

            if logger.isEnabledFor(logging.DEBUG):
                cmd.append('--debug')

            elif logger.isEnabledFor(logging.INFO):
                cmd.append('--verbose')

            cmd.append(user_id)

            self.run(
                cmd,
                as_current_user=as_current_user,
                capture_output=True)

        finally:
            shutil.rmtree(tmpdir)

    def modify_user(
            self,
            user_id,
            password=None,
            password_file=None,
            add_see_also=None,
            del_see_also=None,
            as_current_user=False):

        cmd = [self.name + '-user-mod']

        if password is not None:
            cmd.extend(['--password', password])

        if password_file is not None:
            cmd.extend(['--password-file', password_file])

        if add_see_also:
            cmd.append('--add-see-also')
            cmd.append(add_see_also)

        if del_see_also:
            cmd.append('--del-see-also')
            cmd.append(del_see_also)

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(user_id)

        self.run(
            cmd,
            as_current_user=as_current_user,
            capture_output=True)

    def remove_user(self, user_id, as_current_user=False):

        cmd = [self.name + '-user-del']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(user_id)

        self.run(
            cmd,
            as_current_user=as_current_user,
            capture_output=True)

    def find_user_certs(
            self,
            user_id,
            as_current_user=False):

        cmd = [self.name + '-user-cert-find']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(user_id)

        self.run(
            cmd,
            as_current_user=as_current_user)

    def add_user_cert(self, user_id,
                      cert_data=None,
                      cert_path=None,
                      cert_format='PEM',
                      as_current_user=False):

        cmd = [self.name + '-user-cert-add']

        if cert_path:
            cmd.append('--cert')
            cmd.append(cert_path)

        if cert_format:
            cmd.append('--format')
            cmd.append(cert_format)

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(user_id)

        self.run(
            cmd,
            input=cert_data,
            as_current_user=as_current_user,
            capture_output=True)

    def remove_user_cert(self, user_id, cert_id):

        cmd = [self.name + '-user-cert-del']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(user_id)
        cmd.append(cert_id)

        self.run(cmd)

    def find_user_roles(
            self,
            user_id,
            output_format=None):

        cmd = [self.name + '-user-role-find']

        if output_format:
            cmd.append('--output-format')
            cmd.append(output_format)

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(user_id)

        self.run(cmd)

    def add_user_role(
            self,
            user_id,
            role_id):

        cmd = [self.name + '-user-role-add']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(user_id)
        cmd.append(role_id)

        self.run(cmd)

    def remove_user_role(
            self,
            user_id,
            role_id):

        cmd = [self.name + '-user-role-del']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(user_id)
        cmd.append(role_id)

        self.run(cmd)

    def run(self,
            args,
            input=None,  # pylint: disable=W0622
            as_current_user=False,
            capture_output=False):

        java_home = self.instance.config['JAVA_HOME']
        java_opts = self.instance.config['JAVA_OPTS']

        classpath = [
            pki.server.Tomcat.SHARE_DIR + '/bin/tomcat-juli.jar',
            '/usr/share/java/tomcat-servlet-api.jar',
            pki.server.PKIServer.SHARE_DIR + '/' +
            self.name + '/webapps/' + self.name + '/WEB-INF/lib/*',
            self.instance.common_lib_dir + '/*',
            pki.server.PKIServer.SHARE_DIR + '/lib/*'
        ]

        cmd = []

        # by default run command as systemd user
        if not as_current_user:

            # switch to systemd user if different from current user
            username = pwd.getpwuid(os.getuid()).pw_name
            if username != self.instance.user:
                cmd.extend(['/usr/sbin/runuser', '-u', self.instance.user, '--'])

        cmd.extend([java_home + '/bin/java'])

        cmd.extend([
            '-classpath', os.pathsep.join(classpath),
            '-Djavax.sql.DataSource.Factory=org.apache.commons.dbcp.BasicDataSourceFactory',
            '-Dcatalina.base=' + self.instance.base_dir,
            '-Dcatalina.home=' + pki.server.Tomcat.SHARE_DIR,
            '-Djava.endorsed.dirs=',
            '-Djava.io.tmpdir=' + self.instance.temp_dir,
            '-Djava.util.logging.config.file=' + self.instance.logging_properties,
            '-Djava.util.logging.manager=org.apache.juli.ClassLoaderLogManager'
        ])

        if java_opts:
            opts = java_opts.split(' ')
            non_empty_opts = [opt for opt in opts if opt]
            cmd.extend(non_empty_opts)

        cmd.extend(['org.dogtagpki.server.cli.PKIServerCLI'])

        cmd.extend(args)

        logger.debug('Command: %s', ' '.join(cmd))

        # https://stackoverflow.com/questions/53209127/subprocess-unexpected-keyword-argument-capture-output/53209196
        if capture_output:
            stdout = subprocess.PIPE
        else:
            stdout = None

        try:
            return subprocess.run(
                cmd,
                input=input,
                stdout=stdout,
                check=True)

        except KeyboardInterrupt:
            logger.debug('Server stopped')


class CASubsystem(PKISubsystem):

    def __init__(self, instance):
        super().__init__(instance, 'ca')

    def get_profile_configs(self):

        profiles_dir = os.path.join(self.base_dir, 'profiles')

        profile_configs = []
        for root, _, files in os.walk(profiles_dir):
            for filename in files:
                profile_configs.append(os.path.join(root, filename))

        return profile_configs

    def import_profiles(
            self,
            input_folder=None,
            as_current_user=False):

        cmd = [self.name + '-profile-import']

        if input_folder:
            cmd.extend(['--input-folder', input_folder])

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def find_certs(
            self,
            status=None,
            as_current_user=False):

        cmd = ['ca-cert-find']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        if status:
            cmd.extend(['--status', status])

        self.run(cmd, as_current_user=as_current_user)

    def create_cert(
            self,
            request_id=None,
            profile_id=None,
            cert_type=None,
            key_id=None,
            key_token=None,
            key_algorithm=None,
            signing_algorithm=None,
            serial=None,
            cert_format=None):

        tmpdir = tempfile.mkdtemp()

        try:
            cmd = ['ca-cert-create']

            if logger.isEnabledFor(logging.DEBUG):
                cmd.append('--debug')

            elif logger.isEnabledFor(logging.INFO):
                cmd.append('--verbose')

            if request_id:
                cmd.extend(['--request', request_id])

            if profile_id:
                cmd.extend(['--profile', profile_id])

            if cert_type:
                cmd.extend(['--type', cert_type])

            if key_id:
                cmd.extend(['--key-id', key_id])

            if key_token:
                cmd.extend(['--key-token', key_token])

            if key_algorithm:
                cmd.extend(['--key-algorithm', key_algorithm])

            if signing_algorithm:
                cmd.extend(['--signing-algorithm', signing_algorithm])

            if serial:
                cmd.extend(['--serial', serial])

            if cert_format:
                cmd.extend(['--format', cert_format])

            result = self.run(
                cmd,
                capture_output=True)

        finally:
            shutil.rmtree(tmpdir)

        return result.stdout

    def import_cert(
            self,
            cert_data=None,
            cert_path=None,
            cert_format=None,
            request_id=None,
            profile_id=None):

        tmpdir = tempfile.mkdtemp()

        try:
            if cert_data and not cert_path:
                cert_path = os.path.join(tmpdir, 'cert.crt')
                with open(cert_path, 'w', encoding='utf-8') as f:
                    f.write(cert_data)

            cmd = ['ca-cert-import']

            if logger.isEnabledFor(logging.DEBUG):
                cmd.append('--debug')

            elif logger.isEnabledFor(logging.INFO):
                cmd.append('--verbose')

            if cert_path:
                cmd.extend(['--cert', cert_path])

            if cert_format:
                cmd.extend(['--format', cert_format])

            if request_id:
                cmd.extend(['--request', request_id])

            if profile_id:
                cmd.extend(['--profile', profile_id])

            # run as current user so it can read the input file
            self.run(cmd, as_current_user=True)

        finally:
            shutil.rmtree(tmpdir)

    def remove_cert(self, serial_number, as_current_user=False):

        cmd = ['ca-cert-del']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        cmd.append(serial_number)

        self.run(cmd, as_current_user=as_current_user)

    def find_cert_requests(self, cert=None):

        base_dn = self.config['internaldb.basedn']

        if cert:
            escaped_value = ldap.filter.escape_filter_chars(cert)
            search_filter = '(extdata-req--005fissued--005fcert=%s)' % escaped_value

        else:
            search_filter = '(objectClass=*)'

        con = self.open_database()

        entries = con.ldap.search_s(
            'ou=ca,ou=requests,%s' % base_dn,
            ldap.SCOPE_ONELEVEL,
            search_filter,
            None)

        con.close()

        cert_requests = []
        for entry in entries:
            cert_requests.append(self.create_request_object(entry))

        return cert_requests

    def import_cert_request(
            self,
            request_id=None,
            request_data=None,
            request_path=None,
            request_format=None,
            request_type=None,
            profile_id=None,
            dns_names=None,
            adjust_validity=None):

        if request_path and not request_data:
            with open(request_path, 'r', encoding='utf-8') as f:
                request_data = f.read()

        cmd = ['ca-cert-request-import']

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        if request_format:
            cmd.extend(['--format', request_format])

        if request_type:
            cmd.extend(['--type', request_type])

        if profile_id:
            cmd.extend(['--profile', profile_id])

        if dns_names:
            cmd.extend(['--dns-names', ','.join(dns_names)])

        if adjust_validity:
            cmd.append('--adjust-validity')

        cmd.append('--output-format')
        cmd.append('json')

        if request_id:
            cmd.append(request_id)

        result = self.run(
            cmd,
            input=request_data.encode('utf-8'),
            capture_output=True)

        return json.loads(result.stdout.decode('utf-8'))

    def get_cert_requests(self, request_id):

        base_dn = self.config['internaldb.basedn']

        con = self.open_database()

        entries = con.ldap.search_s(
            'cn=%s,ou=ca,ou=requests,%s' % (request_id, base_dn),
            ldap.SCOPE_BASE,
            '(objectClass=*)',
            None)

        con.close()

        entry = entries[0]
        return self.create_request_object(entry)

    def create_request_object(self, entry):

        attrs = entry[1]

        request = {}
        request['id'] = attrs['cn'][0].decode('utf-8')
        request['type'] = attrs['requestType'][0].decode('utf-8')
        request['status'] = attrs['requestState'][0].decode('utf-8')
        request['request'] = attrs['extdata-cert--005frequest'][0] \
            .decode('utf-8')

        return request

    def enable_subsystem(self, subsystem_id):

        pattern = re.compile(r'subsystem\.(.*)\.id')

        for key in list(self.config.keys()):

            m = pattern.match(key)
            if not m:
                continue

            value = self.config[key]
            if value != subsystem_id:
                continue

            subsystem_number = m.group(1)
            self.config['subsystem.%s.enabled' % subsystem_number] = 'true'

    def disable_subsystem(self, subsystem_id):

        pattern = re.compile(r'subsystem\.(.*)\.id')

        for key in list(self.config.keys()):

            m = pattern.match(key)
            if not m:
                continue

            value = self.config[key]
            if value != subsystem_id:
                continue

            subsystem_number = m.group(1)
            self.config['subsystem.%s.enabled' % subsystem_number] = 'false'

    def get_crl_config(self):

        config = {}

        # find ca.crl.* params
        pattern = re.compile(r'^ca.crl\.([^\.]*)$')

        for key, value in self.config.items():

            m = pattern.match(key)
            if not m:
                continue

            name = m.group(1)
            if name.startswith('_'):
                continue

            config[name] = value

        return config

    def find_crl_issuing_point_ids(self):

        ids = []

        # find ca.crl.<id>.class params
        pattern = re.compile(r'^ca.crl\.([^\.]*)\.class$')

        for key in self.config.keys():

            m = pattern.match(key)
            if not m:
                continue

            ip_id = m.group(1)
            ids.append(ip_id)

        return ids

    def get_crl_issuing_point_config(self, ip_id):

        config = {}

        # find ca.crl.<id>.* params
        pattern = re.compile(r'^ca.crl\.%s\.([^\.]*)' % ip_id)

        for key, value in self.config.items():

            m = pattern.match(key)
            if not m:
                continue

            name = m.group(1)
            config[name] = value

        return config

    def update_crl_issuing_point_config(self, ip_id, config):

        for key, value in config.items():
            param = 'ca.crl.%s.%s' % (ip_id, key)
            pki.util.set_property(self.config, param, value)


class KRASubsystem(PKISubsystem):

    def __init__(self, instance):
        super().__init__(instance, 'kra')


class OCSPSubsystem(PKISubsystem):

    def __init__(self, instance):
        super().__init__(instance, 'ocsp')

    def find_crl_issuing_point(
            self,
            size=None,
            as_current_user=False):

        cmd = [self.name + '-crl-issuingpoint-find']

        if size:
            cmd.extend(['--size', size])

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(cmd, as_current_user=as_current_user)

    def add_crl_issuing_point(
            self,
            cert_chain=None,
            cert_chain_file=None,
            cert_format=None,
            as_current_user=False):

        cmd = [self.name + '-crl-issuingpoint-add']

        if cert_chain_file:
            cmd.extend(['--cert-chain', cert_chain_file])

        if cert_format:
            cmd.extend(['--cert-format', cert_format])

        if logger.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        elif logger.isEnabledFor(logging.INFO):
            cmd.append('--verbose')

        self.run(
            cmd,
            input=cert_chain,
            as_current_user=as_current_user)


class TKSSubsystem(PKISubsystem):

    def __init__(self, instance):
        super().__init__(instance, 'tks')


class TPSSubsystem(PKISubsystem):

    def __init__(self, instance):
        super().__init__(instance, 'tps')


class PKISubsystemFactory(object):

    @classmethod
    def create(cls, instance, name):

        if name == 'ca':
            return CASubsystem(instance)

        if name == 'kra':
            return KRASubsystem(instance)

        if name == 'ocsp':
            return OCSPSubsystem(instance)

        if name == 'tks':
            return TKSSubsystem(instance)

        if name == 'tps':
            return TPSSubsystem(instance)

        return PKISubsystem(instance, name)
