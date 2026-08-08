"""
Seed-профили для известных сайтов.

Создаются при старте приложения, если их нет в БД.
Идемпотентно: повторные вызовы не создают дубликатов.
"""

import json
from datetime import datetime

from models import db, SiteProfile


F6S_PROGRAMS_INSTRUCTION = {
    "schema_version": 1,
    "domain": "f6s.com",
    "path_prefix": "/programs",
    "card_selector": ".result-item",
    "fields": {
        "title_selector": ".result-description .title a",
        "link_selector": ".result-description .title a",
        "text_selectors": [".subtitle", ".details", ".result-extra"],
        "text_fallback": "card_inner_text"
    },
    "detail": {
        "enabled": True,
        "expand_button_selector": "#description-toggle",
        "full_text_selector": "#description-expanded",
        "fallback_selectors": [
            "#description-collapsed",
            "main",
            "article",
            "[role='main']",
            "#main"
        ]
    },
    "pagination": {
        "strategy": "html_fragment_url",
        "url_template": None,
        "keep_original_query": True,
        "drop_query_params": ["page", "page_alt"],
        "force_query_params": {"page_alt": "1"},
        "add_if_missing": {"sort": "open"},
        "page_param": "page",
        "first_page_is_target": True
    },
    "stop_conditions": {
        "max_pages": 1500,
        "max_cards": 20000,
        "stop_on_empty_page": True,
        "stop_on_no_new_cards": True
    },
    "validation": {
        "min_cards_first_page": 1,
        "max_empty_title_ratio": 0.2,
        "max_empty_url_ratio": 0.2,
        "min_text_length": 0
    },
    "auth_markers": ["sign in", "log in", "create account"],
    "notes": "Seed-профиль для F6S programs. Пагинация через HTML-фрагменты page=N&page_alt=1.",
    "generated_at": datetime.utcnow().isoformat(),
    "generator_model": "seed"
}


def ensure_f6s_seed_profile():
    """
    Создаёт seed-профиль для f6s.com/programs, если его ещё нет.
    
    Идемпотентно: проверяет наличие по (domain, path_prefix).
    """
    domain = F6S_PROGRAMS_INSTRUCTION["domain"]
    path_prefix = F6S_PROGRAMS_INSTRUCTION["path_prefix"]
    
    existing = SiteProfile.query.filter_by(
        domain=domain,
        path_prefix=path_prefix
    ).first()
    
    if existing:
        return existing
    
    profile = SiteProfile(
        domain=domain,
        path_prefix=path_prefix,
        instructions_json=json.dumps(F6S_PROGRAMS_INSTRUCTION, ensure_ascii=False),
        version=1,
        is_active=True,
        fail_count=0,
        last_success_at=None,
        last_failure_at=None,
        last_error=None
    )
    
    db.session.add(profile)
    db.session.commit()
    
    return profile