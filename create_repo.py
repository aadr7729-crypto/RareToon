import requests, json

try:
    data = {'name': 'raretoontrail00', 'private': True, 'description': 'RareToons bot'}
    r = requests.post('https://api.github.com/user/repos', json=data, timeout=15)
    print(f"Status: {r.status_code}")
    print(f"Body: {r.text[:1000]}")
    if r.status_code == 201:
        repo = r.json()
        print(f"Repo URL: {repo.get('html_url')}")
        print(f"SSH URL: {repo.get('ssh_url')}")
        print(f"Clone URL: {repo.get('clone_url')}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
