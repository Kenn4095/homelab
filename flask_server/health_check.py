#!/usr/bin/env python3
import sys
import time
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

URL = 'http://127.0.0.1:5000/'
RETRIES = 5
DELAY = 1.0


def check(url=URL, retries=RETRIES, delay=DELAY):
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers={'User-Agent': 'health-check/1.0'})
            with urlopen(req, timeout=5) as resp:
                status = resp.getcode()
                body = resp.read(200).decode('utf-8', errors='replace')
                print(f'OK: {url} -> {status}')
                print('Snippet:')
                print(body)
                return 0
        except HTTPError as e:
            print(f'HTTP error: {e.code} {e.reason}')
            return 2
        except URLError as e:
            print(f'URLError (attempt {attempt}/{retries}): {e.reason}')
        except Exception as e:
            print(f'Error (attempt {attempt}/{retries}): {e}')
        if attempt < retries:
            time.sleep(delay)
    print(f'Failed to reach {url} after {retries} attempts')
    return 3


if __name__ == '__main__':
    rc = check()
    sys.exit(rc)
