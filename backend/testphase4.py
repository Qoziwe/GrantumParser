# test_phase4.py
from app import app
from parser import _classify_block, _check_auth_required
from playwright.sync_api import sync_playwright

def test_classify():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.new_page()
        
        # Тест captcha
        page.set_content("<html><body>hcaptcha challenge</body></html>")
        is_blocked, reason, marker = _classify_block(page)
        print(f"Captcha test: blocked={is_blocked}, reason={reason}, marker={marker}")
        assert is_blocked and reason == "captcha"
        
        # Тест cloudflare
        page.set_content("<html><body>Cloudflare Ray ID: 12345</body></html>")
        is_blocked, reason, marker = _classify_block(page)
        print(f"Cloudflare test: blocked={is_blocked}, reason={reason}, marker={marker}")
        assert is_blocked and reason == "cloudflare"
        
        # Тест auth_required
        instruction = {
            "card_selector": ".card",
            "auth_markers": ["sign in", "log in"]
        }
        page.set_content("<html><body><h1>Please sign in</h1></body></html>")
        auth_req = _check_auth_required(page, instruction)
        print(f"Auth required test: {auth_req}")
        assert auth_req
        
        page.close()
        print("Все тесты пройдены!")

if __name__ == "__main__":
    test_classify()