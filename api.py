import responder
import requests
from prometheus_client import Counter, Summary, start_http_server
import time
import asyncio
import concurrent.futures
import os
import json
import data
import socket
from urllib.parse import urlparse

# Prometheus metrics
COMPLETED_REQUEST_COUNTER = Counter('dingy_pings_completed', 'Count of completed dinghy ping requests')
FAILED_REQUEST_COUNTER = Counter('dingy_pings_failed', 'Count of failed dinghy ping requests')
REQUEST_TIME = Summary('dinghy_request_processing_seconds', 'Time spent processing request')

api = responder.API(title="Dinghy Ping", version="1.0", openapi="3.0.0", docs_route="/docs")

# For local mac docker image creation and testing, switch to host.docker.internal
redis_host = os.getenv("REDIS_HOST", default="127.0.0.1")


@api.route("/")
def dinghy_html(req, resp):
    """Index route to Dinghy-ping input html form"""
    resp.content = api.template(
        'ping_input.html',
        get_all_pinged_urls=_get_all_pinged_urls()
    )


@api.route("/dinghy/ping/domains")
async def ping_multiple_domains(req, resp):
    """
    Async process to test multiple domains and return JSON with results
    Post request data example
    {
      "domains": [
        {
          "protocol": "https",
          "domain": "google.com",
          "headers: { "header1": "valule" }
        },
        {
          "protocol": "https",
          "domain": "microsoft.com"
        }
      ]
    }

    Return results
    {
      "domains": [
        {
          "protocol": "https",
          "domain": "google.com",
          "domain_response_code": "200",
          "domain_response_time_ms": "30.0ms"
          "
        },
        {
          "protocol": "https",
          "domain": "microsoft.com"
          "domain_response_code": "200",
          "domain_response_time_ms": "200.1ms"
        }
      ]
    }
    """

    results = []

    def build_domain_results(protocol, request_domain, results, headers):
        domain_response_code, domain_response_text, domain_response_time_ms = _process_request(protocol, request_domain, req.params, headers)
        results.append({
            "protocol": protocol,
            "domain": request_domain,
            "domain_response_code": domain_response_code,
            "domain_response_time_ms": domain_response_time_ms
        })

    def gather_results(data):
        for domain in data['domains']:
            protocol = domain['protocol']
            request_domain = domain['domain']
            headers = domain['headers']
            build_domain_results(protocol, request_domain, results, headers)

    resp.media = {"domains_response_results": results, "wait": gather_results(await req.media())}


@api.route("/dinghy/ping/{protocol}/{domain}")
def domain_response_html(req, resp, *, protocol, domain):
    """
    API endpoint for sending a request to a domain via user specified protocol
    response containts status_code, body text and response_time_ms
    """

    headers = {}
    domain_response_code, domain_response_text, domain_response_time_ms = (
        _process_request(protocol, domain, req.params, headers)
    )

    resp.content = api.template(
            'ping_response.html',
            domain=domain,
            domain_response_code=domain_response_code,
            domain_response_text=domain_response_text,
            domain_response_time_ms=domain_response_time_ms
    )


@api.route("/dinghy/form-input")
def form_input(req, resp):
    """Dinghy-ping html input form for http connection"""
    url = urlparse(req.params['url'])
    if 'headers' in req.params.keys():
        headers = json.loads(req.params['headers'])
    else:
        headers = {}
    if url.scheme == "":
        scheme_notes = "Scheme not given, defaulting to https"
    else:
        scheme_notes = f'Scheme {url.scheme} provided'

    domain_response_code, domain_response_text, domain_response_time_ms = (
        _process_request(url.scheme, url.netloc + url.path, url.query, headers)
    )

    resp.content = api.template(
            'ping_response.html',
            request=f'{req.params["url"]}',
            scheme_notes=scheme_notes,
            domain_response_code=domain_response_code,
            domain_response_text=domain_response_text,
            domain_response_time_ms=domain_response_time_ms
    )

@api.route("/dinghy/form-input-tcp-connetion-test")
def form_input_tcp_connection_test(req, resp):
    """Dinghy-ping html input form for tcp connection"""
    # refactor with https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.create_connection 
    
    def tcp_ping_client(tcp_endpoint, tcp_port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        try:
            s.setblocking(0)
            s.connect_ex((tcp_endpoint, tcp_port))
            connection_status = "Able to connect!"
            peer_info = s.getpeername()
            s.close()
            return {
                "connection_status": connection_status,
                "peer_info": peer_info 
            }
        except socket.timeout as err:
            print(f'Timeout, {tcp_endpoint} on {tcp_port}')
            connection_status = "Unable to connect, timeout after 3 sec"
            peer_info = "None"
            return {
                "connection_status": connection_status,
                "peer_info": peer_info 
            }
    
    tcp_endpoint = urlparse(req.params['tcp-endpoint'])
    tcp_port = urlparse(req.params['tcp-port'])
    resp.content = api.template(
            'ping_response_tcp_conn.html',
            request=f'{req.params["tcp-endpoint"]}',
            port=f'{req.params["tcp-port"]}',
            connection_results = tcp_ping_client(tcp_endpoint, tcp_port)
    )

@REQUEST_TIME.time()
def _process_request(protocol, domain, params, headers):
    """
    Internal method to run request process, takes protocol and domain for input
    """

    if protocol == "":
        protocol = "https"

    domain_response_code = ""
    domain_response_text = ""
    domain_response_time_ms = ""

    try:
        r = requests.get(f'{protocol}://{domain}', params=params, timeout=5, headers=headers)
        COMPLETED_REQUEST_COUNTER.inc()
    except requests.exceptions.Timeout as err:
        domain_response_text = f'Timeout: {err}'
        FAILED_REQUEST_COUNTER.inc()
        return domain_response_code, domain_response_text, domain_response_time_ms
    except requests.exceptions.TooManyRedirects as err:
        domain_response_text = f'TooManyRedirects: {err}'
        FAILED_REQUEST_COUNTER.inc()
        return domain_response_code, domain_response_text, domain_response_time_ms
    except requests.exceptions.RequestException as err:
        domain_response_text = f'RequestException: {err}'
        FAILED_REQUEST_COUNTER.inc()
        return domain_response_code, domain_response_text, domain_response_time_ms

    domain_response_code = r.status_code
    domain_response_text = r.text
    domain_response_time_ms = r.elapsed.microseconds / 1000

    d = data.DinghyData(redis_host, domain_response_code, domain_response_time_ms, r.url)
    d.save_ping()

    return domain_response_code, domain_response_text, domain_response_time_ms

def _get_all_pinged_urls():
    """Get pinged URLs from Dinghy-ping data module"""
    p = data.DinghyData(redis_host)

    return p.get_all_pinged_urls()


if __name__ == '__main__':
    start_http_server(8000)
    api.run(address="0.0.0.0", port=80, debug=True)
