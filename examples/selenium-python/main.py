"""Selenium (Python) attaching to the container over CDP.

Selenium attaches to the already-running Chrome via `debuggerAddress`. Two Selenium
tells: the chromedriver `cdc_` globals (isSeleniumChromeDefault) and the CDP
Runtime.enable leak (isAutomatedWithCDP).

The `cdc_` globals ARE clearable (stripped below via an init script). But
chromedriver enables the CDP Runtime domain and there is no supported way to stop
it, so `isAutomatedWithCDP` stays true as long as chromedriver is in the loop. For
a FULL pass, drop chromedriver: use a raw-CDP driver (see ../python-raw-cdp) or
SeleniumBase CDP Mode. This example shows the best you can do with classic Selenium.

    pip install -r requirements.txt
    python main.py

CDP_ADDRESS defaults to 127.0.0.1:9222. Selenium Manager (bundled with Selenium 4)
downloads a matching chromedriver automatically.
"""

import os

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

CDP_ADDRESS = os.environ.get("CDP_ADDRESS", "127.0.0.1:9222")

options = Options()
options.add_experimental_option("debuggerAddress", CDP_ADDRESS)

driver = webdriver.Chrome(options=options)
try:
    # Strip chromedriver's cdc_ globals before the page can read them.
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": "for(const k of Object.keys(window)){"
            "if(/cdc_/.test(k)){try{delete window[k]}catch(e){}}}"
        },
    )
    driver.get("https://deviceandbrowserinfo.com/are_you_a_bot")
    driver.save_screenshot("result.png")
    print("saved result.png (note: isAutomatedWithCDP remains — see the module docstring)")
finally:
    driver.quit()
