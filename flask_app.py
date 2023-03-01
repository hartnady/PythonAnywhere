from datetime import datetime
from flask import Flask, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy

from io import BytesIO, StringIO
from pdfminer.high_level import extract_text_to_fp
import pdfminer.layout
import openai, json, traceback

app = Flask(__name__)
app.config.from_object('config')
app.config['DEBUG'] = True
openai.api_key = app.config['OPEN_AI_KEY']
slack_webhook_url = app.config['SLACK_WEBHOOK_URI']

db = SQLAlchemy(app)

def gpt_complete(prompt):
    params = {
        "prompt":prompt,
        "engine":"text-davinci-003",
        "temperature":0.1,
        "max_tokens":4097-len(prompt)-150
    }
    response = openai.Completion.create(**params)

    try:
        response = openai.Completion.create(**params)

        if 'error' in response:
            error_message = response['error']['message']
            return f"GPT Error: {error_message}"
        else:
            completion_text = response.choices[0].text.strip()
            return completion_text
    except Exception as e:
        error_traceback = traceback.format_exc()
        return f"REST Error: {str(e)}\n{error_traceback}"

@app.route('/', methods=['GET'])
def root_get():
    return 'Welcome to PDF Extract. Please use HTTP POST to submit PDF data for conversion to a JSON-L document.'

@app.route('/', methods=['POST'])
def root_post():
    # Get the binary data from the POST request
    pdf_data = request.get_data()
    output_string = StringIO()
    extract_text_to_fp(BytesIO(pdf_data), output_string, laparams=pdfminer.layout.LAParams())
    text = output_string.getvalue()

    gpt_compl_text = 'Acting as an entity recognition expert convert the following TEXT using this template:\n'
    gpt_compl_text+= '{ "Order": {"operation_no":"", "doc_no": "", "date": "", "contact_person": "", "OrderItems": [{"position":"","article_no":"","product":"","quantity":"","price":"","uom":"","date":""}]}}\n'
    gpt_compl_text+= 'TEXT:\n'

    try:
        gpt_resp = gpt_complete(gpt_compl_text + text)
        json_dict = json.loads(gpt_resp)
        pretty_json_str = json.dumps(json_dict, indent=2)
        return pretty_json_str, 200, {'Content-Type': 'text/plain'}

    except Exception as e:
        abort(500,description=str(e))

@app.route('/slack', methods=['GET'])
def slack_get():
    return 'Welcome to GPT for Slack. Please POST x-www-form-urlencoded data to /slack/events to enqueue a GPT request.', 200

@app.route('/slack/events', methods=['GET'])
def slack_events_get():
    return 'Welcome to GPT for Slack. Please POST x-www-form-urlencoded data to /slack/events to enqueue a GPT request.', 200

@app.route("/slack/events", methods=["POST"])
def slack_events_post():

    mimetype = request.mimetype
    if mimetype == 'application/x-www-form-urlencoded':
        payload = request.form
    else:
        abort(500,description="This API only accepts mime-type of application/x-www-form-urlencoded")

    if 'text' not in payload: abort(500,description='"text" value missing from form data')
    if 'user_id' not in payload: abort(500,description='"user_id" value missing from form data')

    text = payload['text']

    if text == 'help':
        return_text =  '\n>`/gpt [your_prompt]` will queue a *new* request with the GPT service. e.g. `/gpt give me a chocolate cake recipe`'
        return_text += '\n>`/gpt [request_id]` will provide the status of a *queued* request e.g. `/gpt 25`'
        return_text += '\n>`/gpt [request_id] [your_next_prompt]` will continue a *previous* dialogue e.g. `/gpt 25 now give me baking instructions`'
        count = Job.query.filter_by(state='queued').count()
        return_text += '\nMax prompt length is *3000 characters*'
        return_text += f'\nThere are currently *{count} items* in the queue awaiting processing.'
        return return_text, 200

    if len(text) > 3000:
        return 'Prompt too long. Max characters in prompt = 3000', 200

    if len(text) < 30 and text.isdigit():
        int_id = int(text)
        queue = Job.query.filter_by(id=int_id)
        if job := queue.first():
            count = Job.query.filter_by(state='queued').count()
            return 'Request:'+str(job.id) + '\nUser: <@'+str(job.user_id)+'>\nText: '+str(job.message)+'\nState: ' + str(job.state) + '\nQueue size: ' + str(count), 200
        else:
            return 'Request:'+str(text) + ' not found.', 200

    user_id = payload['user_id']

    text_parts = text.split(' ')
    if len(text_parts) > 1:
        if text_parts[0].isdigit():
            prev = Job.query.filter_by(user_id=user_id).order_by(Job.id.desc())
            if job := prev.first():
                everything_after_the_request_id = ' '.join(text_parts[1:])
                text = job.message + '\n' + job.response + '\n' + everything_after_the_request_id
                text = text[-3000:] if len(text) > 3000 else text

    ip = request.headers.get("X-Real-Ip", "")
    now = datetime.utcnow().isoformat()
    job_id = f"{ip}{now}"

    data = Job(slug=job_id,message=text,webhook_url=slack_webhook_url,user_id=user_id)
    db.session.add(data)
    db.session.commit()

    count = Job.query.filter_by(state='queued').count()

    return f'Request placed in queue with `id: {data.id}`.\nThere are {count} items in the queue including yours.\nTo query the status of this request, type `/gpt {data.id}`', 200

@app.route("/slack/events/<string:job_id>", methods=["GET"])
def slack_events_status_get(job_id):
    data = Job.query.filter_by(id=job_id).first()
    return jsonify(
        {
            "id": data.id,
            "slug": data.slug,
            "state": data.state,
            "result": data.result,
            "message": data.message,
            "user_id": data.user_id,
            "webhook_url": data.webhook_url,
            "response": data.response
        }
    )

class Job(db.Model):
    __tablename__ = "jobs"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(64), nullable=False)
    webhook_url = db.Column(db.String(500), nullable=True)
    message = db.Column(db.String(65535), nullable=True)
    user_id = db.Column(db.String(20), nullable=True)
    state = db.Column(db.String(10), nullable=False, default="queued")
    result = db.Column(db.Integer, default=0)
    response = db.Column(db.String(65535), nullable=True)