#!/usr/bin/env python3
"""
Тестовый скрипт для отладки логики получения ссылок на видео
"""
import json
from telegram import _get_authenticated_video_url, _find_first_url

# Примеры структур видео из MAX
test_cases = [
    {
        "name": "Видео с baseUrl в file",
        "attach": {
            "id": "video123",
            "_type": "VIDEO",
            "file": {
                "baseUrl": "https://cdn.max.com/video",
                "id": "abc123def456"
            }
        }
    },
    {
        "name": "Видео с прямой URL",
        "attach": {
            "id": "video456",
            "_type": "VIDEO",
            "url": "https://storage.example.com/video.mp4"
        }
    },
    {
        "name": "Видео без URL (проблемное)",
        "attach": {
            "id": "video789",
            "_type": "VIDEO",
            "mimeType": "video/mp4",
            "name": "myfile.mp4",
            "file": {
                "id": "someid123",
                "size": 5000000
            }
        }
    },
    {
        "name": "Видео с nested preview",
        "attach": {
            "id": "video999",
            "_type": "VIDEO",
            "preview": {
                "baseUrl": "https://api.max.com/media",
                "id": "preview_id_xyz",
                "file": {
                    "baseUrl": "https://cdn.max.com",
                    "id": "actual_file_id"
                }
            }
        }
    }
]

print("=" * 70)
print("🧪 ТЕСТИРОВАНИЕ ЛОГИКИ ПОЛУЧЕНИЯ URL ВИДЕО")
print("=" * 70)

max_token = "test_token_abc123"

for test_case in test_cases:
    print(f"\n📝 Тест: {test_case['name']}")
    print(f"   Структура: {json.dumps(test_case['attach'], indent=2, ensure_ascii=False)[:100]}...")
    
    print(f"\n   🔍 _find_first_url():")
    found_url = _find_first_url(test_case['attach'])
    if found_url:
        print(f"      ✅ Найдена: {found_url}")
    else:
        print(f"      ❌ Не найдена")
    
    print(f"\n   🔓 _get_authenticated_video_url():")
    auth_url = _get_authenticated_video_url(test_case['attach'], max_token)
    if auth_url:
        print(f"      ✅ Получена: {auth_url}")
    else:
        print(f"      ❌ Не получена")
    
    print("   " + "-" * 66)

print("\n" + "=" * 70)
print("✅ Тестирование завершено")
