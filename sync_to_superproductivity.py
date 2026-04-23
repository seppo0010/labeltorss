import requests
import xml.etree.ElementTree as ET
import sys
import time

# Configuration
RSS_URL = 'http://localhost:5002/labeltorss/rss.xml'
SUPER_PRODUCTIVITY_API_URL = 'http://127.0.0.1:3876'
PROJECT_NAME = 'Newsletters'

# Email to Tag Mapping
SENDER_TAG_MAP = {
    'someunpleasant@substack.com': 'Mindel',
    'causalinf+difference-in-differences@substack.com': 'Cunningham',
    'hola@a1000.ar': 'A1000',
    'noteconomics@substack.com': 'Ajzenman',
    'pricetheory@substack.com': 'Hendrickson',
    'aisnakeoil@substack.com': 'Kapoor and Narayanan'
}

def get_tag_name(email):
    """Determines the tag name based on the sender email."""
    if not email:
        return None
    
    # Check direct mapping
    if email in SENDER_TAG_MAP:
        return SENDER_TAG_MAP[email]
    
    # Check domain mapping
    if email.endswith('@cenital.com'):
        return 'Cenital'
    
    return None

def get_project_id(project_name):
    """Fetches the project ID for a given project name."""
    try:
        response = requests.get(f'{SUPER_PRODUCTIVITY_API_URL}/projects', params={'query': project_name})
        response.raise_for_status()
        data = response.json()
        if data.get('ok') and data.get('data'):
            for project in data['data']:
                if project['title'] == project_name:
                    return project['id']
        return None
    except Exception as e:
        print(f"Error fetching project ID: {e}")
        return None

def get_tag_id(tag_name):
    """Fetches the tag ID for a given tag name, or creates it if it doesn't exist."""
    try:
        # 1. Try to find existing tag
        response = requests.get(f'{SUPER_PRODUCTIVITY_API_URL}/tags', params={'query': tag_name})
        response.raise_for_status()
        data = response.json()
        if data.get('ok') and data.get('data'):
            for tag in data['data']:
                if tag['title'] == tag_name:
                    return tag['id']
        
        # 2. If not found, create it (Wait: The local API might not have a POST /tags endpoint documented, 
        # but let's assume it doesn't or we should only use existing tags. 
        # Looking at 3.01-API.md, there is no POST /tags in Local REST API section, 
        # only GET /tags. However, Plugin API has addTag. 
        # Let's check if POST /tags works or just return None if not found.)
        print(f"Tag '{tag_name}' not found in Super Productivity.")
        return None
    except Exception as e:
        print(f"Error fetching tag ID: {e}")
        return None

def task_exists(title, project_id):
    """Checks if a task with the given title already exists in the project (including archived)."""
    try:
        response = requests.get(f'{SUPER_PRODUCTIVITY_API_URL}/tasks', params={
            'query': title,
            'projectId': project_id,
            'source': 'all',
            'includeDone': 'true'
        })
        response.raise_for_status()
        data = response.json()
        if data.get('ok') and data.get('data'):
            for task in data['data']:
                if task['title'] == title:
                    return True
        return False
    except Exception as e:
        print(f"Error checking existing tasks: {e}")
        return False

def add_task(title, project_id, tag_ids=None):
    """Adds a task to Super Productivity."""
    try:
        if task_exists(title, project_id):
            print(f"Task already exists: {title}")
            return False

        payload = {
            'title': title,
            'projectId': project_id,
            'plannedAt': int(time.time() * 1000),
        }
        if tag_ids:
            payload['tagIds'] = tag_ids

        response = requests.post(f'{SUPER_PRODUCTIVITY_API_URL}/tasks', json=payload)
        response.raise_for_status()
        result = response.json()
        if result.get('ok'):
            print(f"Successfully added task: {title} with tags: {tag_ids}")
            return True
        else:
            print(f"Failed to add task: {title} - {result.get('error')}")
            return False
    except Exception as e:
        print(f"Error adding task: {e}")
        return False

def main():
    # 1. Fetch RSS
    try:
        print(f"Fetching RSS from {RSS_URL}...")
        response = requests.get(RSS_URL)
        response.raise_for_status()
        rss_content = response.content
    except Exception as e:
        print(f"Error fetching RSS: {e}")
        sys.exit(1)

    # 2. Parse RSS/Atom
    try:
        root = ET.fromstring(rss_content)
        # RSS items
        items = root.findall('.//item')
        if not items:
            # Try Atom entries
            items = root.findall('{http://www.w3.org/2005/Atom}entry')
            is_atom = True
        else:
            is_atom = False

        print(f"Found {len(items)} items in feed.")
    except Exception as e:
        print(f"Error parsing RSS: {e}")
        sys.exit(1)

    # 3. Get Project ID
    project_id = get_project_id(PROJECT_NAME)
    if not project_id:
        print(f"Could not find project: {PROJECT_NAME}")
        sys.exit(1)
    print(f"Found project '{PROJECT_NAME}' with ID: {project_id}")

    # 4. Add tasks
    for item in items:
        if is_atom:
            title_elem = item.find('{http://www.w3.org/2005/Atom}title')
            author_elem = item.find('{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}email')
            if author_elem is None:
                author_elem = item.find('{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name')
        else:
            title_elem = item.find('title')
            author_elem = item.find('author')
        
        title = title_elem.text if title_elem is not None else None
        author_email = author_elem.text if author_elem is not None else None
        
        if title:
            tag_ids = []
            tag_name = get_tag_name(author_email)
            if tag_name:
                tag_id = get_tag_id(tag_name)
                if tag_id:
                    tag_ids.append(tag_id)
            
            add_task(title, project_id, tag_ids)

if __name__ == "__main__":
    main()
