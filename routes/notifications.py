# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify
from auth import (
    login_required, get_current_user,
    get_notifications, get_unread_notification_count,
    mark_notification_read, mark_all_notifications_read,
    delete_notification, delete_all_notifications,
)

bp = Blueprint('notifications', __name__)


@bp.route('/api/notifications')
@login_required
def api_notifications():
    user = get_current_user()
    notifs = get_notifications(user['username'])
    return jsonify(notifs)


@bp.route('/api/notifications/unread_count')
@login_required
def api_notifications_unread_count():
    user = get_current_user()
    cnt = get_unread_notification_count(user['username'])
    return jsonify({'count': cnt})


@bp.route('/api/notifications/read', methods=['POST'])
@login_required
def api_notification_read():
    user = get_current_user()
    data = request.get_json() or {}
    notif_id = data.get('notif_id')
    if not notif_id:
        return jsonify({'error': '缺少 notif_id'}), 400
    ok = mark_notification_read(int(notif_id), user['username'])
    return jsonify({'ok': ok})


@bp.route('/api/notifications/read_all', methods=['POST'])
@login_required
def api_notifications_read_all():
    user = get_current_user()
    ok = mark_all_notifications_read(user['username'])
    return jsonify({'ok': ok})


@bp.route('/api/notifications/delete', methods=['POST'])
@login_required
def api_notification_delete():
    user = get_current_user()
    data = request.get_json() or {}
    notif_id = data.get('notif_id')
    if not notif_id:
        return jsonify({'error': '缺少 notif_id'}), 400
    try:
        ok = delete_notification(int(notif_id), user['username'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': ok})


@bp.route('/api/notifications/delete_all', methods=['POST'])
@login_required
def api_notifications_delete_all():
    user = get_current_user()
    try:
        deleted = delete_all_notifications(user['username'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True, 'deleted': deleted})
