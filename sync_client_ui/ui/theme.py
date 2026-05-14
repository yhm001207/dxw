LIGHT = {
    'name': 'light',
    'primary': '#1677FF',
    'primary_hover': '#4096FF',
    'primary_bg': '#E6F4FF',
    'primary_active': '#0958D9',
    'success': '#0FC6C2',
    'success_bg': '#E6FFFB',
    'warning': '#FF7D00',
    'warning_bg': '#FFF7E6',
    'error': '#F53F3F',
    'error_bg': '#FFF1F0',
    'info': '#1677FF',
    'bg': '#F5F7FA',
    'card_bg': '#FFFFFF',
    'sidebar_bg': '#FFFFFF',
    'topbar_bg': '#FFFFFF',
    'text_primary': '#1D2129',
    'text_secondary': '#4E5969',
    'text_muted': '#86909C',
    'border': '#E5E6EB',
    'divider': '#F0F0F5',
    'hover_bg': '#F5F7FA',
    'active_bg': '#E6F4FF',
}

DARK = {
    'name': 'dark',
    'primary': '#1677FF',
    'primary_hover': '#4096FF',
    'primary_bg': '#1A3A6B',
    'primary_active': '#0958D9',
    'success': '#0FC6C2',
    'success_bg': '#163A3A',
    'warning': '#FF7D00',
    'warning_bg': '#3A2A16',
    'error': '#F53F3F',
    'error_bg': '#3A1616',
    'info': '#1677FF',
    'bg': '#141414',
    'card_bg': '#1F1F1F',
    'sidebar_bg': '#1A1A1A',
    'topbar_bg': '#1F1F1F',
    'text_primary': '#F5F5F5',
    'text_secondary': '#C9CDD4',
    'text_muted': '#6B6F77',
    'border': '#2B2B2B',
    'divider': '#252525',
    'hover_bg': '#2A2A2A',
    'active_bg': '#1A3A6B',
}

SCI_FI = {
    'name': 'sci_fi',
    'primary': '#00EEFF',
    'primary_hover': '#33F4FF',
    'primary_bg': 'rgba(0,238,255,0.08)',
    'primary_active': '#00CCDD',
    'success': '#00FF9C',
    'success_bg': 'rgba(0,255,156,0.08)',
    'warning': '#FFD600',
    'warning_bg': 'rgba(255,214,0,0.08)',
    'error': '#FF2D55',
    'error_bg': 'rgba(255,45,85,0.08)',
    'info': '#00EEFF',
    'bg': '#0A0E17',
    'card_bg': 'rgba(17,24,39,0.85)',
    'sidebar_bg': 'rgba(10,14,23,0.95)',
    'topbar_bg': 'rgba(17,24,39,0.9)',
    'text_primary': '#E0F7FF',
    'text_secondary': '#8892B0',
    'text_muted': '#5A6480',
    'border': 'rgba(0,238,255,0.12)',
    'divider': 'rgba(0,238,255,0.06)',
    'hover_bg': 'rgba(0,238,255,0.05)',
    'active_bg': 'rgba(0,238,255,0.1)',
}

MODES = [LIGHT, DARK, SCI_FI]
_current = LIGHT


def get(key):
    return _current.get(key, '')


def set_mode(mode_name):
    global _current
    for m in MODES:
        if m['name'] == mode_name:
            _current = m
            return


def is_dark():
    return _current['name'] in ('dark', 'sci_fi')


def current_mode():
    return _current['name']


def next_mode():
    names = [m['name'] for m in MODES]
    idx = names.index(_current['name'])
    return names[(idx + 1) % len(names)]
