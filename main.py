import requests
import json
import colorama


class Logger:
    ENABLED = False
    INITIALIZED = False

    @staticmethod
    def initialize():
        if not Logger.INITIALIZED:
            colorama.init()
            Logger.INITIALIZED = True

    @staticmethod
    def info(message: str):
        if not Logger.ENABLED:
            return
        Logger.initialize()
        print(f"{colorama.Fore.GREEN}[INFO] {message}{colorama.Fore.RESET}")


class AuthenticationConfigError(Exception):
    def __init__(self, message):
        super().__init__(message)


class CloudflareAPIError(Exception):
    def __init__(self, status_code, message):
        super().__init__(f"CloudflareAPI responded with {status_code}: {message}")


class CloudflareAPI:
    BASE_URL = "https://api.cloudflare.com/client/v4/"

    def __init__(self, authentication: dict):
        self.is_token_authorized = authentication.get("use-token", False)
        self.email = authentication.get("email", "")
        self.api_auth = authentication.get("api-auth", "")

        if not self.api_auth or (self.is_token_authorized and not self.email):
            raise AuthenticationConfigError("Please check your authentication configuration and correct it!")

    def getAuthHeaders(self, headers):
        if self.is_token_authorized:
            return {
                "Authorization": "Bearer " + self.api_auth,
                **headers
            }
        return {
            "X-Auth-Email": self.email,
            "X-Auth-Key": self.api_auth,
            **headers
        }

    def make_request(self, method, endpoint, data=None, headers=None):
        if not data:
            data = {}
        if not headers:
            headers = {}
        response = requests.request(method, CloudflareAPI.BASE_URL + endpoint, headers=self.getAuthHeaders(headers), json=data)
        if response.status_code == 200:
            return response.json()
        else:
            raise CloudflareAPIError(response.status_code, " | ".join([data["message"] for data in response.json().get("errors", [])]))

    def get_domain_name(self, zone_id):
        return self.make_request("GET", f"zones/{zone_id}")["result"]["name"]

    def check_record(self, zone_id, name, type):
        data = self.make_request("GET", f"zones/{zone_id}/dns_records?per_page=100&type={type}")
        if type == "SRV":
            record = list(filter(lambda rec: rec["name"].endswith(name), data["result"]))
        else:
            record = list(filter(lambda rec: rec["name"] == name, data["result"]))
        if not record:  # If list is empty, it means there are no record with supplied name
            return None
        return record[0]

    def add_record(self, zone_id, data):
        self.make_request("POST", f"zones/{zone_id}/dns_records", data=data)

    def update_record(self, zone_id, record_id, data):
        self.make_request("PUT", f"zones/{zone_id}/dns_records/{record_id}", data=data)


def handle_http_record(domain_name, zone_id, name, ttl, proxied, record_data):
    to_send = {
        "type": "A",
        "name": domain_name if name == "@" or name == "" else name + "." + domain_name,
        "proxied": proxied,
        "ttl": ttl,
        "content": public_ip
    }

    if not (record := api.check_record(zone_id, to_send["name"], "A")): # Insert new record
        Logger.info("Creating a new record with given name")
        api.add_record(zone_id, to_send)
    else: # Update existing record
        if record["content"] == public_ip and record["ttl"] == ttl and record["proxied"] == proxied:
            Logger.info("Not updating existing record as it is set to correct IPv4")
            return
        Logger.info("Updating an existing record with given name")
        api.update_record(zone_id, record["id"], to_send)


def handle_srv_record(domain_name, zone_id, name, ttl, proxied, record_data):
    handle_http_record(domain_name, zone_id, name, ttl, proxied, None)

    name = domain_name if name == "@" or name == "" else name + "." + domain_name
    proto = f"_{record_data['proto'].lower()}"

    to_send = {
        "type": "SRV",
        "data": {
            "name": name,
            "service": record_data["service"],
            "proto": proto,
            "priority": record_data["priority"],
            "weight": record_data["weight"],
            "port": record_data["port"],
            "target": name
        }
    }

    if not (record := api.check_record(zone_id, name, "SRV")): # Insert new record
        Logger.info("Creating a new record with given name")
        api.add_record(zone_id, to_send)
    else: # Update existing record
        if record["data"] == to_send["data"]:
            Logger.info("Not updating existing record as it is set to correct data")
            return
        Logger.info("Updating an existing record with given name")
        api.update_record(zone_id, record["id"], to_send)


HANDLES = {
    "A": handle_http_record,
    "SRV": handle_srv_record
}


def update_record(domain_name, zone_id, record):
    name = record["name"]
    ttl = record.get("ttl", 1)
    record_type = record.get("type", None)
    proxied = record.get("proxied", False) if record_type == "SRV" else record.get("proxied", True)

    if not record_type:
        raise RuntimeError("Record type is not supported")

    Logger.info(f"Running record update for {name}.{domain_name}")
    HANDLES[record_type](domain_name, zone_id, name, ttl, proxied, record)


def run_ddns(ddns):
    domain_name = api.get_domain_name(ddns["zone-id"])
    Logger.info("Running DDNS for "+domain_name)
    for record_data in ddns.get("records", []):
        update_record(domain_name, ddns["zone-id"], record_data)


if __name__ == "__main__":
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    api = CloudflareAPI(config.get("authentication", {}))
    public_ip = dict(line.split("=") for line in requests.get("https://1.1.1.1/cdn-cgi/trace").text[:-1].split("\n"))["ip"]
    Logger.ENABLED = config.get("logging", False)

    for ddns_data in config.get("dns", []):
        run_ddns(ddns_data)
