# Database setup
username = "your_username_here"
password = "your_database_password_here"
hostname = f"{username}.mysql.pythonanywhere-services.com"
databasename = f"{username}$asyncinweb"

SQLALCHEMY_DATABASE_URI = (
    f"mysql://{username}:{password}@{hostname}/{databasename}"
)
SQLALCHEMY_ENGINE_OPTIONS = {"pool_recycle": 299}
SQLALCHEMY_TRACK_MODIFICATIONS = False

SLACK_API_KEY = "your_slack_api_key_here"
SLACK_WEBHOOK_URI = "your_slack_webhook_url_here"
OPEN_AI_KEY = "your_open_ai_key_here"
