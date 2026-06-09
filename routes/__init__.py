# -*- coding: utf-8 -*-
"""routes 包 - 注册所有 Blueprint"""


def register_all(app):
    """注册所有 Blueprint 到 Flask app"""
    from routes.auth_routes import bp as auth_bp
    from routes.core import bp as core_bp
    from routes.whitelist import bp as whitelist_bp
    from routes.admin import bp as admin_bp
    from routes.messaging import bp as messaging_bp
    from routes.notifications import bp as notifications_bp
    from routes.monitoring import bp as monitoring_bp
    from routes.files import bp as files_bp
    from routes.execution import bp as execution_bp
    from routes.shared_folders import bp as shared_bp
    from routes.profiles import bp as profiles_bp
    from routes.training import bp as training_bp
    from routes.ai_chat import bp as ai_bp
    from routes.sync import bp as sync_bp
    from routes.speed_test import bp as speed_bp
    from routes.ai_image import bp as ai_image_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(core_bp)
    app.register_blueprint(whitelist_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(messaging_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(monitoring_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(execution_bp)
    app.register_blueprint(shared_bp)
    app.register_blueprint(profiles_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(sync_bp)
    app.register_blueprint(speed_bp)
    app.register_blueprint(ai_image_bp)
