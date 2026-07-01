"""Selenium (Python) attaching to the container over CDP.

Selenium attaches to the already-running Chrome via `debuggerAddress` instead of
launching its own. The container does not set a User-Agent, so we set it with a
raw CDP command.

    pip install -r requirements.txt
    python main.py

CDP_ADDRESS defaults to 127.0.0.1:9222.

Note: Selenium still needs a chromedriver matching the container's Chrome
version. Selenium Manager (bundled with Selenium 4) downloads it automatically.
"""

import os

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

CDP_ADDRESS = os.environ.get("CDP_ADDRESS", "127.0.0.1:9222")
REAL_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)

options = Options()
options.add_experimental_option("debuggerAddress", CDP_ADDRESS)

driver = webdriver.Chrome(options=options)
try:
    driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": REAL_UA})
    driver.get("https://bot.sannysoft.com/")
    driver.save_screenshot("sannysoft.png")
    print("saved sannysoft.png — UA:", driver.execute_script("return navigator.userAgent"))
finally:
    driver.quit()
