import logging
import time
import random
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

class CloudflareBypass:
    
    @staticmethod
    def attempt_turnstile_click(driver):
        try:
            shadow_script = """
            let target = document.querySelector("input[type='checkbox']");
            if (!target) {
                document.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) {
                        let cb = el.shadowRoot.querySelector("input[type='checkbox']");
                        if (cb) target = cb;
                    }
                });
            }
            return target;
            """
            element = driver.execute_script(shadow_script)
            
            if element:
                logging.info("  >> Turnstile checkbox found. Clicking...")
                action = ActionChains(driver)
                action.move_by_offset(random.randint(10, 100), random.randint(10, 100))
                action.perform()
                time.sleep(0.5)
                element.click()
                return True
                
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for iframe in iframes:
                src = iframe.get_attribute("src") or ""
                if "challenges" in src:
                    driver.switch_to.frame(iframe)
                    cb = driver.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
                    if cb:
                        cb.click()
                        driver.switch_to.default_content()
                        return True
                    driver.switch_to.default_content()
        except:
            pass
        return False

    @staticmethod
    def handle_challenge(driver, timeout=45, screenshot_path=None):
        if "Just a moment" not in driver.title and "challenge" not in driver.page_source.lower():
            return True
        
        logging.warning("⚠️ Cloudflare Challenge Detected! Solving...")
        start = time.time()
        
        while time.time() - start < timeout:
            if "Just a moment" not in driver.title:
                logging.info("✓ Cloudflare bypassed.")
                return True
            CloudflareBypass.attempt_turnstile_click(driver)
            time.sleep(3)
        
        logging.error("❌ Cloudflare timed out.")
        if screenshot_path:
            try:
                driver.save_screenshot(screenshot_path)
                logging.info(f"Screenshot saved to {screenshot_path}")
            except:
                pass
        return False
