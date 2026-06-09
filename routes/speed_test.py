# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify, render_template, Response
import os
from auth import login_required

bp = Blueprint('speed_test', __name__)


@bp.route('/speed_test')
def speed_test():
    return render_template('speed_test.html')


@bp.route('/api/speed_test_data')
def speed_test_data():
    size_mb = int(request.args.get('mb', 10))
    size_mb = min(size_mb, 100)
    total = size_mb * 1024 * 1024

    def generate():
        sent = 0
        chunk = os.urandom(64 * 1024)
        while sent < total:
            to_send = min(len(chunk), total - sent)
            yield chunk[:to_send]
            sent += to_send

    return Response(
        generate(),
        mimetype='application/octet-stream',
        headers={
            'Content-Length': str(total),
            'Content-Disposition': f'attachment; filename="speedtest_{size_mb}mb.bin"',
            'Cache-Control': 'no-cache',
        },
    )
