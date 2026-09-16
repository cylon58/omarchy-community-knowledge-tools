"""Owned destinations; external project selection always needs explicit consent."""


def routes():
    return {'routing_version': 1, 'destinations': {
        'ledger': {'provider': 'github', 'repository_id': '1373429914',
                   'slug': 'cylon58/omarchy-community-knowledge'},
        'toolkit': {'provider': 'github', 'repository_id': '1373429982',
                    'slug': 'cylon58/omarchy-community-knowledge-tools'},
        'upstream': None, 'plugin': None,
    }}
