import os
import json
import time
import uuid
import logging
import requests

log = logging.getLogger('dxw_sync')


class CloudAPI:
    def __init__(self, server_url='http://localhost:5000'):
        self.server_url = server_url.rstrip('/')
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': 'DXW-Sync-Client/2.0'})

    def login(self, username, password):
        resp = self._session.post(f'{self.server_url}/login', json={
            'username': username, 'password': password
        }, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('ok') is True:
                return True
            raise Exception(data.get('error', '登录失败'))
        raise Exception(f'HTTP {resp.status_code}')

    def test_connection(self, server_url, username, password, timeout=5):
        s = requests.Session()
        try:
            resp = s.post(f'{server_url.rstrip("/")}/login', json={
                'username': username, 'password': password
            }, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('ok') is True:
                    return True, '连接成功'
                return False, data.get('error', '认证失败')
            return False, f'HTTP {resp.status_code}'
        except requests.exceptions.ConnectionError:
            return False, '无法连接到服务器'
        except requests.exceptions.Timeout:
            return False, '连接超时'
        except Exception as e:
            return False, str(e)

    def get_changes(self, folder, local_files):
        files_list = [{'relative_path': k, 'mtime': v[0], 'size': v[1]} for k, v in local_files.items()]
        resp = self._session.post(f'{self.server_url}/api/sync/changes', json={
            'base_path': folder, 'files': files_list
        }, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return {
                'upload': data.get('newer_on_client', []) + data.get('missing_server', []),
                'download': data.get('newer_on_server', []) + data.get('missing_client', []),
            }
        if resp.status_code == 401:
            raise PermissionError('session expired')
        raise Exception(f'get_changes failed: HTTP {resp.status_code}')

    def upload(self, folder, rel_path, file_obj, file_size, version_retention_count=0, version_retention_mode='count', version_retention_days=0):
        chunk_size = 30 * 1024 * 1024
        if file_size <= chunk_size:
            files = {'file': (os.path.basename(rel_path), file_obj)}
            data = {'target_dir': folder, 'relative_path': rel_path.replace(os.sep, '/'), 'version_retention_count': version_retention_count, 'version_retention_mode': version_retention_mode, 'version_retention_days': version_retention_days}
            resp = self._session.post(f'{self.server_url}/api/upload', data=data, files=files, timeout=300)
            if resp.status_code == 401:
                raise PermissionError('session expired')
            if resp.status_code != 200:
                raise Exception(f'upload failed: HTTP {resp.status_code}')
            return resp.json()

        upload_id = str(uuid.uuid4())
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        for i in range(total_chunks):
            file_obj.seek(i * chunk_size)
            chunk_data = file_obj.read(chunk_size)
            files = {'chunk': (str(i), chunk_data)}
            data = {
                'upload_id': upload_id,
                'chunk_index': i,
                'total_chunks': total_chunks,
                'filename': os.path.basename(rel_path),
                'target_dir': folder,
                'relative_path': rel_path.replace(os.sep, '/'),
            }
            resp = self._session.post(f'{self.server_url}/api/upload_chunk', data=data, files=files, timeout=300)
            if resp.status_code == 401:
                raise PermissionError('session expired')
            if resp.status_code != 200:
                raise Exception(f'chunk upload failed at {i}: HTTP {resp.status_code}')
        resp = self._session.post(f'{self.server_url}/api/upload_complete', json={
            'upload_id': upload_id,
            'target_dir': folder,
            'filename': os.path.basename(rel_path),
            'total_chunks': total_chunks,
            'relative_path': rel_path.replace(os.sep, '/'),
            'version_retention_count': version_retention_count,
            'version_retention_mode': version_retention_mode,
            'version_retention_days': version_retention_days,
        }, timeout=60)
        if resp.status_code != 200:
            raise Exception(f'merge failed: HTTP {resp.status_code}')
        return resp.json()

    def download_batch(self, folder, paths):
        resp = self._session.post(f'{self.server_url}/api/sync/download_batch', json={
            'folder': folder, 'paths': paths
        }, timeout=300)
        if resp.status_code == 401:
            raise PermissionError('session expired')
        if resp.status_code != 200:
            raise Exception(f'download failed: HTTP {resp.status_code}')
        return resp.content

    def list_dir(self, path):
        resp = self._session.get(f'{self.server_url}/api/files', params={'path': path}, timeout=10)
        if resp.status_code == 401:
            raise PermissionError('session expired')
        if resp.status_code == 200:
            data = resp.json()
            return data.get('items', [])
        return []

    def delete_file(self, path):
        resp = self._session.post(f'{self.server_url}/api/delete_file', json={'path': path}, timeout=30)
        if resp.status_code == 401:
            raise PermissionError('session expired')
        return resp.status_code == 200

    def download_file(self, path):
        resp = self._session.get(f'{self.server_url}/api/download_file', params={'path': path}, timeout=60)
        if resp.status_code == 401:
            raise PermissionError('session expired')
        if resp.status_code == 200:
            return resp.content
        data = resp.json()
        raise Exception(data.get('error', f'HTTP {resp.status_code}'))

    def get_versions(self, file_path):
        """List version history of a file"""
        resp = self._session.post(f'{self.server_url}/api/versions/list', json={
            'path': file_path
        }, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return {'versions': []}

    def restore_version(self, file_path, version_name):
        """Restore file to a specific version"""
        resp = self._session.post(f'{self.server_url}/api/versions/restore', json={
            'path': file_path, 'version': version_name
        }, timeout=30)
        if resp.status_code == 200:
            return True, resp.json()
        data = resp.json()
        return False, data.get('error', 'Restore failed')

    def get_user_dir_path(self):
        resp = self._session.get(f'{self.server_url}/api/user_dir', timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('path', '')
        return ''

