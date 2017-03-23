from changeme.scanners.http_basic_auth import HTTPBasicAuthScanner
from changeme.scanners.http_get import HTTPGetScanner
from changeme.scanners.http_post import HTTPPostScanner
from changeme.scanners.http_raw_post import HTTPRawPostScanner
import logging
from lxml import html
from netaddr import *
import re
import requests


class HttpFingerprint:
    def __init__(self, target, url, port, ssl, headers, cookies, config, creds):
        self.target = target
        self.url = url
        self.port = port
        self.ssl = ssl
        self.headers = headers
        self.cookies = cookies
        self.config = config
        self.creds = creds
        self.logger = logging.getLogger('changeme')

    def __getstate__(self):
        state = self.__dict__
        state['logger'] = None  # Need to clear the logger when serializing otherwise mp.Queue blows up
        return state

    def __setstate__(self, d):
        self.__dict__ = d
        self.logger = logging.getLogger('changeme')

    def __hash__(self):
        return hash(str(self.target) + str(self.url) + str(self.port) + str(self.ssl) + str(self.headers) + str(self.cookies))

    def __eq__(self, other):
        if self.target == other.target and self.url == other.url and self.port == other.port and self.ssl == other.ssl and self.headers == other.headers and self.cookies == other.cookies:
            return True

    def full_URL(self):
        proto = 'https' if self.ssl else 'http'
        return '%s://%s:%s%s' % (proto, self.target, self.port, self.url)

    def fingerprint(self):
        scanners = list()
        s = requests.Session()
        url = self.full_URL()

        try:
            res = s.get(
                url,
                timeout=self.config.timeout,
                verify=False,
                proxies=self.config.proxy,
                headers=self.headers,
                cookies=self.cookies
            )
        except Exception as e:
            self.logger.debug('Failed to connect to %s' % url)
            return

        for cred in self.creds:
            if self.ismatch(cred, res):

                csrf = self._get_csrf_token(res, cred)
                if cred['auth'].get('csrf', False) and not csrf:
                    self.logger.error('Missing required CSRF token')
                    return

                sessionid = self._get_session_id(res, cred)
                if cred['auth'].get('sessionid') and not sessionid:
                    self.logger.error("Missing session cookie %s for %s" % (cred['auth'].get('sessionid'), res.url))
                    return

                for pair in cred['auth']['credentials']:
                    for u in cred['auth']['url']:  # pass in the auth url
                        u = '%s%s' % (HTTPGetScanner.get_base_url(res.url), u)
                        self.logger.debug('Building %s %s:%s' % (cred['name'], pair['username'], pair['password']))

                        if cred['auth']['type'] == 'get':
                            scanners.append(HTTPGetScanner(cred, u, pair['username'], pair['password'], self.config, s.cookies))
                        elif cred['auth']['type'] == 'post':
                            scanners.append(HTTPPostScanner(cred, u, pair['username'], pair['password'], self.config, s.cookies, csrf))
                        elif cred['auth']['type'] == 'raw_post':
                            scanners.append(HTTPRawPostScanner(cred, u, pair['username'], pair['password'], self.config, s.cookies, csrf, pair['raw']))
                        elif cred['auth']['type'] == 'basic_auth':
                            scanners.append(HTTPBasicAuthScanner(cred, u, pair['username'], pair['password'], self.config, s.cookies))

        return scanners

    def _get_csrf_token(self, res, cred):
        name = cred['auth'].get('csrf', False)
        if name:
            tree = html.fromstring(res.content)
            try:
                csrf = tree.xpath('//input[@name="%s"]/@value' % name)[0]
            except:
                self.logger.error(
                    'Failed to get CSRF token %s in %s' % (str(name), str(res.url)))
                return False
            self.logger.debug('Got CSRF token %s: %s' % (name, csrf))
        else:
            csrf = False

        return csrf

    def _get_session_id(self, res, cred):
        cookie = cred['auth'].get('sessionid', False)

        if cookie:
            try:
                value = res.cookies[cookie]
                self.logger.debug('Got session cookie value: %s' % value)
            except:
                self.logger.error(
                    'Failed to get %s cookie from %s' % (cookie, res.url))
                return False
            return {cookie: value}
        else:
            self.logger.debug('No cookie')
            return False

    def ismatch(self, cred, response):
        match = False
        if cred['protocol'] == 'http':
            fp = cred['fingerprint']
            basic_auth = fp.get('basic_auth_realm', None)
            if basic_auth and basic_auth in response.headers.get('WWW-Authenticate', list()):
                self.logger.info('%s basic auth matched: %s' % (cred['name'], basic_auth))
                match = True

            server = response.headers.get('Server', None)
            fp_server = fp.get('server_header', None)
            if fp_server and server and fp_server in server:
                self.logger.debug('%s server header matched: %s' % (cred['name'], fp_server))
                match = True

            body = fp.get('body', None)
            if body:
                for b in body:
                    if re.search(b, response.text):
                        match = True
                        self.logger.info('%s body matched: %s' % (cred['name'], b))
                    elif body:
                        self.logger.debug('%s body not matched' % cred['name'])
                        match = False

        return match

    @staticmethod
    def build_fingerprints(targets, creds, config):
        fingerprints = list()
        logger = logging.getLogger('changeme')
        # Build a set of unique fingerprints
        for target in targets:
            for c in creds:
                if not c['protocol'] == 'http':
                    continue
                fp = c['fingerprint']
                for url in fp.get('url'):
                    logger.debug(url)
                    if not isinstance(target, IPAddress) and ":" in target and not int(target.split(":")[1]) == int(c.get('default_port')):
                        # Only scan open ports from an nmap file
                        continue
                    elif not isinstance(target, IPAddress) and ":" in target:
                        # strip port from nmap target
                        target = target.split(":")[0]

                    hfp = HttpFingerprint(
                        target,
                        url,
                        c.get('default_port', 80),
                        c.get('ssl'),
                        fp.get('headers', None),
                        fp.get('cookie', None),
                        config,
                        creds,
                    )
                    logger.debug('Adding %s to fingerprint list' % hfp.full_URL())
                    fingerprints.append(hfp)

        return fingerprints
