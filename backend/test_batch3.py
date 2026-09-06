import requests
import json
import os
import sys

BASE = 'http://localhost:8080/api/v1/wiki'

def test_batch3():
    print('=' * 60)
    print('Batch 3 API Tests')
    print('=' * 60)

    page_id = ''

    # 1. Create test page
    print('\n--- 1. Create Test Page ---')
    r = requests.post(f'{BASE}/pages/create', json={
        'title': 'Batch3TestPage',
        'content': '# Test\n\nBatch3 test content\n\n{{> embedded test}}',
        'tags': ['test', 'batch3']
    })
    data = r.json()
    print(f'  Create page: success={data.get("success")}')
    page_id = data.get('page', {}).get('page_id', '')
    print(f'  Page ID: {page_id}')

    # 2. Full text search
    print('\n--- 2. Full Text Search ---')
    r = requests.post(f'{BASE}/search/fulltext', json={
        'query': 'test', 'page_size': 5, 'fuzzy': True, 'highlight': True
    })
    data = r.json()
    print(f'  Fulltext search: success={data.get("success")}, total={data.get("total", 0)}')

    # 3. Search stats
    r = requests.get(f'{BASE}/search/stats')
    data = r.json()
    print(f'  Search stats: {json.dumps(data, ensure_ascii=False)[:200]}')

    # 4. Rebuild index
    print('\n--- 3. Rebuild Search Index ---')
    r = requests.post(f'{BASE}/search/rebuild-index')
    data = r.json()
    print(f'  Rebuild index: success={data.get("success")}, indexed={data.get("indexed_count", 0)}')

    # 5. Wiki Templates
    print('\n--- 4. Wiki Templates ---')
    r = requests.get(f'{BASE}/templates')
    data = r.json()
    print(f'  List templates: success={data.get("success")}, count={len(data.get("templates", []))}')
    for t in data.get('templates', [])[:3]:
        print(f'    - {t["name"]} ({t["category"]})')

    # 6. Template categories
    r = requests.get(f'{BASE}/templates/categories')
    data = r.json()
    print(f'  Categories: {data.get("categories", [])}')

    # 7. Apply template
    r_get = requests.get(f'{BASE}/templates')
    templates = r_get.json().get('templates', [])
    if templates:
        tpl_id = templates[0]['template_id']
        r = requests.post(f'{BASE}/templates/apply', json={
            'template_id': tpl_id, 'title': 'TemplateTestPage', 'space_id': 'default'
        })
        data = r.json()
        print(f'  Apply template: success={data.get("success")}')

    # 8. Create custom template
    r = requests.post(f'{BASE}/templates/create', json={
        'name': 'CustomTestTemplate',
        'content': '# {title}\n\nDate: {date}\n\n',
        'description': 'Custom test template',
        'category': 'custom'
    })
    data = r.json()
    print(f'  Create custom template: success={data.get("success")}')

    # 9. Page embed resolve
    print('\n--- 5. Page Embed Resolve ---')
    r = requests.post(f'{BASE}/embeds/resolve', json={
        'content': 'Test content {{> nonexistent}} and {{> Batch3TestPage}}',
        'max_depth': 3
    })
    data = r.json()
    print(f'  Embed resolve: success={data.get("success")}')
    resolved = data.get('resolved_content', '')
    print(f'  Resolved (first 150): {resolved[:150]}')

    # 10. Embed references
    if page_id:
        r = requests.get(f'{BASE}/embeds/references/{page_id}')
        data = r.json()
        print(f'  Embed references: success={data.get("success")}, count={len(data.get("references", []))}')

    # 11. Attachment upload
    print('\n--- 6. Attachments ---')
    if page_id:
        test_content = b'Hello, this is a test attachment file content.'
        r = requests.post(f'{BASE}/attachments/upload/{page_id}',
            files={'file': ('test_file.txt', test_content, 'text/plain')},
            data={'author': 'test', 'description': 'Test attachment'}
        )
        data = r.json()
        print(f'  Upload attachment: success={data.get("success")}')
        attach_id = data.get('attachment', {}).get('attach_id', '')

        # List attachments
        r = requests.get(f'{BASE}/attachments/{page_id}')
        data = r.json()
        print(f'  List attachments: success={data.get("success")}, count={len(data.get("attachments", []))}')

        # Attachment stats
        r = requests.get(f'{BASE}/attachments/stats')
        data = r.json()
        print(f'  Attachment stats: {json.dumps(data, ensure_ascii=False)[:150]}')

        # Delete attachment
        if attach_id:
            r = requests.delete(f'{BASE}/attachments/delete/{attach_id}')
            data = r.json()
            print(f'  Delete attachment: success={data.get("success")}')
    else:
        print('  Skipped (no page_id)')

    # 12. Page export
    print('\n--- 7. Page Export ---')
    if page_id:
        for fmt in ['markdown', 'html']:
            r = requests.post(f'{BASE}/export', json={
                'page_id': page_id, 'format': fmt, 'resolve_embeds': True
            })
            if r.status_code == 200:
                ct = r.headers.get('content-type', '')
                if 'json' in ct:
                    data = r.json()
                    print(f'  Export {fmt}: success={data.get("success")}')
                else:
                    print(f'  Export {fmt}: file downloaded (status={r.status_code})')
            else:
                print(f'  Export {fmt}: status={r.status_code}')
    else:
        print('  Skipped (no page_id)')

    # 13. Recycle bin
    print('\n--- 8. Recycle Bin ---')
    r = requests.post(f'{BASE}/pages/create', json={
        'title': 'RecycleBinTestPage', 'content': 'To be deleted'
    })
    del_page_id = r.json().get('page', {}).get('page_id', '')

    if del_page_id:
        # Soft delete
        r = requests.post(f'{BASE}/recycle-bin/soft-delete/{del_page_id}?deleted_by=tester')
        data = r.json()
        print(f'  Soft delete: success={data.get("success")}')

        # List recycle bin
        r = requests.get(f'{BASE}/recycle-bin/list')
        data = r.json()
        print(f'  List recycle bin: success={data.get("success")}, count={data.get("total", 0)}')

        # Restore
        r = requests.post(f'{BASE}/recycle-bin/restore/{del_page_id}')
        data = r.json()
        print(f'  Restore: success={data.get("success")}')

        # Soft delete again and permanent delete
        r = requests.post(f'{BASE}/recycle-bin/soft-delete/{del_page_id}?deleted_by=tester')
        r = requests.delete(f'{BASE}/recycle-bin/permanent/{del_page_id}')
        data = r.json()
        print(f'  Permanent delete: success={data.get("success")}')
    else:
        print('  Skipped (no del_page_id)')

    # 14. Audit log
    print('\n--- 9. Audit Log ---')
    r = requests.post(f'{BASE}/audit-logs/query', json={'page': 1, 'page_size': 10})
    data = r.json()
    print(f'  Query audit logs: success={data.get("success")}, total={data.get("total", 0)}')

    r = requests.get(f'{BASE}/audit-logs/stats')
    data = r.json()
    print(f'  Audit stats: {json.dumps(data, ensure_ascii=False)[:150]}')

    # 15. Email notification
    print('\n--- 10. Email Notification ---')
    r = requests.get(f'{BASE}/email/config')
    data = r.json()
    print(f'  Get email config: success={data.get("success")}')

    r = requests.post(f'{BASE}/email/config', json={
        'enabled': False, 'smtp_host': 'smtp.test.com', 'smtp_port': 587
    })
    data = r.json()
    print(f'  Update email config: success={data.get("success")}')

    r = requests.get(f'{BASE}/email/queue')
    data = r.json()
    print(f'  Email queue: success={data.get("success")}')

    # 16. API Key Management
    print('\n--- 11. API Key Management ---')
    r = requests.post(f'{BASE}/api-keys/create', json={
        'name': 'TestAPIKey', 'permissions': ['read', 'write'], 'expires_days': 30
    })
    data = r.json()
    print(f'  Create API key: success={data.get("success")}')
    raw_key = data.get('api_key', '')
    key_id = data.get('key_id', '')

    r = requests.get(f'{BASE}/api-keys/list')
    data = r.json()
    print(f'  List API keys: success={data.get("success")}, count={len(data.get("keys", []))}')

    if raw_key:
        r = requests.post(f'{BASE}/api-keys/validate', json={
            'api_key': raw_key, 'required_permission': 'read'
        })
        data = r.json()
        print(f'  Validate API key: valid={data.get("valid")}')

    if key_id:
        r = requests.post(f'{BASE}/api-keys/revoke/{key_id}')
        data = r.json()
        print(f'  Revoke API key: success={data.get("success")}')

        r = requests.delete(f'{BASE}/api-keys/delete/{key_id}')
        data = r.json()
        print(f'  Delete API key: success={data.get("success")}')

    r = requests.get(f'{BASE}/api-keys/stats')
    data = r.json()
    print(f'  API key stats: {json.dumps(data, ensure_ascii=False)[:150]}')

    # 17. Backup & Restore
    print('\n--- 12. Backup & Restore ---')
    r = requests.post(f'{BASE}/backups/create', json={
        'description': 'Test backup', 'backup_type': 'wiki_only', 'created_by': 'tester'
    })
    data = r.json()
    print(f'  Create backup: success={data.get("success")}')
    backup_id = data.get('backup_id', '')

    r = requests.get(f'{BASE}/backups/list')
    data = r.json()
    print(f'  List backups: success={data.get("success")}, count={len(data.get("backups", []))}')

    r = requests.get(f'{BASE}/backups/stats')
    data = r.json()
    print(f'  Backup stats: {json.dumps(data, ensure_ascii=False)[:150]}')

    if backup_id:
        r = requests.delete(f'{BASE}/backups/delete/{backup_id}')
        data = r.json()
        print(f'  Delete backup: success={data.get("success")}')

    # 18. Soft delete page (for recycle bin integration)
    print('\n--- 13. Soft Delete Page (toolbar) ---')
    if page_id:
        r = requests.post(f'{BASE}/recycle-bin/soft-delete/{page_id}?deleted_by=tester')
        data = r.json()
        print(f'  Soft delete page: success={data.get("success")}')

        # Restore it back
        r = requests.post(f'{BASE}/recycle-bin/restore/{page_id}')
        data = r.json()
        print(f'  Restore page: success={data.get("success")}')

    print('\n' + '=' * 60)
    print('Batch 3 API Tests Complete!')
    print('=' * 60)


if __name__ == '__main__':
    try:
        test_batch3()
    except Exception as e:
        print(f'\nTest failed with error: {e}')
        import traceback
        traceback.print_exc()
