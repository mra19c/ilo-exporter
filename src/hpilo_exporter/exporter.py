"""
Pulls data from specified iLO and presents as Prometheus metrics
"""
from __future__ import print_function
from _socket import gaierror
import sys
import os
import hpilo

import time
import prometheus_metrics
from BaseHTTPServer import BaseHTTPRequestHandler
from BaseHTTPServer import HTTPServer
from SocketServer import ForkingMixIn
from prometheus_client import generate_latest, Summary
from urlparse import parse_qs
from urlparse import urlparse


# Create a metric to track time spent and requests made.
REQUEST_TIME = Summary('request_processing_seconds',
                       'Time spent processing request')


def print_err(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


class ForkingHTTPServer(ForkingMixIn, HTTPServer):
    max_children = 30
    timeout = 30


class RequestHandler(BaseHTTPRequestHandler):
    """
    Endpoint handler
    """

    def return_error(self):
        self.send_response(500)
        self.end_headers()

    def _health(self):
        # get health at glance
        health_at_glance = self.ilo.get_embedded_health()['health_at_a_glance']

        if health_at_glance is not None:
            for key, value in health_at_glance.items():
                for status in value.items():
                    if status[0] == 'status':
                        gauge = 'hpilo_{}_gauge'.format(key)
                        if status[1].upper() == 'OK':
                            prometheus_metrics.gauges[gauge].labels(
                                product_name=self.product_name, server_name=self.server_name).set(0)
                        elif status[1].upper() == 'DEGRADED':
                            prometheus_metrics.gauges[gauge].labels(
                                product_name=self.product_name, server_name=self.server_name).set(1)
                        else:
                            prometheus_metrics.gauges[gauge].labels(
                                product_name=self.product_name, server_name=self.server_name).set(2)

    def _host_power(self):
        _power = self.ilo.get_host_power_status()
        _gauge = 'hpilo_{}_gauge'.format('host_power')
        if _power == 'ON':
            prometheus_metrics.gauges[_gauge].labels(
                product_name=self.product_name,
                server_name=self.server_name).set(0)
        else:
            prometheus_metrics.gauges[_gauge].labels(
                product_name=self.product_name,
                server_name=self.server_name).set(1)

    def _firmware(self):
        _version = self.ilo.get_fw_version()["firmware_version"]
        prometheus_metrics.hpilo_firmware_version.labels(
            product_name=self.product_name,
            server_name=self.server_name).set(_version)

    def _power_readings(self):
        (_present, _) = self.ilo.get_power_readings()['present_power_reading']
        prometheus_metrics.hpilo_present_power_reading.labels(
            product_name=self.product_name, server_name=self.server_name).set(_present)

    def do_GET(self):
        """
        Process GET request

        :return: Response with Prometheus metrics
        """
        # get parameters from the URL
        _url = urlparse(self.path)

        if _url.path == self.server.endpoint:
            query_components = parse_qs(urlparse(self.path).query)
            _host = None
            _port = None
            _user = None
            _password = None

            try:
                _host = query_components['target'][0]
            except KeyError as e:
                print_err("** missing parameter 'target' in url **")
                self.return_error()
                return

            try:
                _port = os.environ['ilo_port']
                _user = os.environ['ilo_user']
                _password = os.environ['ilo_password']
            except KeyError as e:
                print_err("** missing environment parameter %s **" % e)
                self.return_error()
                return

            self.server_name = _host
            self.ilo = None
            if _host and _user and _password and _port:
                try:
                    self.ilo = hpilo.Ilo(hostname=_host,
                                         login=_user,
                                         password=_password,
                                         port=int(_port), timeout=10)
                except hpilo.IloLoginFailed:
                    print("ILO login failed")
                    self.return_error()
                except gaierror:
                    print("ILO invalid address or port")
                    self.return_error()
                except hpilo.IloCommunicationError as e:
                    print(e)

            # this will be used to return the total amount of time the request
            # took
            start_time = time.time()

            try:
                self.product_name = self.ilo.get_product_name()
            except BaseException:
                self.product_name = "Unknown HP Server"

            self._health()
            self._host_power()
            self._firmware()
            self._power_readings()

            # get the amount of time the request took
            REQUEST_TIME.observe(time.time() - start_time)

            # generate and publish metrics
            metrics = generate_latest(prometheus_metrics.registry)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(metrics)

            return

        # tell users the /metrics endpoint
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write("""<html>
        <head><title>HP iLO Exporter</title></head>
        <body>
        <h1>HP iLO Exporter</h1>
        <p>Visit <a href="/metrics">Metrics</a> to use.</p>
        </body>
        </html>""")


class ILOExporterServer(object):
    """
    Basic server implementation that exposes metrics to Prometheus
    """

    def __init__(self, address='0.0.0.0', port=8080, endpoint="/metrics"):
        self._address = address
        self._port = port
        self.endpoint = endpoint

    def print_info(self):
        print_err("Starting exporter on: http://{}:{}{}".format(self._address,
                                                                self._port,
                                                                self.endpoint))
        print_err("Press Ctrl+C to quit")

    def run(self):
        self.print_info()

        server = ForkingHTTPServer((self._address, self._port), RequestHandler)
        server.endpoint = self.endpoint

        try:
            while True:
                server.handle_request()
        except KeyboardInterrupt:
            print_err("Killing exporter")
            server.server_close()
