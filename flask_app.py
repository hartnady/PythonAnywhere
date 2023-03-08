from datetime import datetime
from flask import Flask, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from io import BytesIO, StringIO
from pdfminer.high_level import extract_text_to_fp
import pdfminer.layout
import openai, json, traceback, requests

app = Flask(__name__)
app.config.from_object('config')
app.config['DEBUG'] = True
openai.api_key = app.config['OPEN_AI_KEY']
slack_webhook_url = app.config['SLACK_WEBHOOK_URI']
slack_api_key = app.config['SLACK_API_KEY']
chat_bot_user_id = app.config['CHAT_BOT_USER_ID']

db = SQLAlchemy(app)

#====================
#PDF EXTRACT METHODS:
#====================

def gpt_complete(prompt):
    params = {
        "prompt":prompt,
        "engine":"text-davinci-003",
        "temperature":0.1, #please keep temperature low for PDF entity extraction
        "max_tokens":4097-len(prompt)-150 #please do use an imported module to calculate accurate token size (it slows down processing)
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

#======================
#GPT SLACK BOT METHODS:
#======================

def get_last_5_requests(user_id):
    prev_requests = Job.query.filter_by(user_id=user_id).order_by(Job.id.desc()).limit(5)
    prev_req_ids = list()
    for prev_job in prev_requests: prev_req_ids.append(str(prev_job.id))
    prev_req_ids_str = ', '.join(prev_req_ids)
    return prev_req_ids_str

def responder(job):
    msg = job.message.replace('\n','\n>')
    resp = job.response.replace('\n','\n>')
    return f"*Request:* `{job.id}`\n*IP & Timestamp*: `{job.slug}`\n*<@{job.user_id}> asked:*\n>{msg}\n*GPT Responded:*\n>{resp}"

#======================
#FLASK ROUTINGS (APIs):
#======================

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

@app.route('/slack/post_to_channel', methods=['POST'])
def slack_post_to_channel_post():

    mimetype = request.mimetype
    if mimetype == 'application/x-www-form-urlencoded':
        data = request.form
        if 'payload' not in data: abort(500,description='"payload" value missing from form data')
        payload = json.loads(data['payload'])
    else:
        abort(500,description="This API only accepts mime-type of application/x-www-form-urlencoded")

    job_id = payload['actions'][0]['value']
    job = Job.query.filter_by(id=job_id).first()

    outbound_payload = { "channel": f"{job.channel_id}", "text": responder(job) }

    headers = headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": "Bearer " + slack_api_key
    }

    response = requests.post('https://slack.com/api/chat.postMessage', json=outbound_payload, headers=headers)

    if response.status_code == 200:
        return '', 200
    else:
        return 'Error posting message. Please check server logs.', 200

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
    user_id = payload['user_id']
    if 'channel_id' in payload: channel_id = payload['channel_id']

    if text.strip() == '':
        return 'No prompt detected. Try again using syntax: `/gpt your question here` or `/gpt help` for help.', 200

    if text == 'help':
        return_text =  ' \n>`/gpt [your_prompt]` will enqueue a *new* request with the GPT service. e.g. `/gpt give me a chocolate cake recipe`'
        return_text += '\n>`/gpt [request_id]` will provide the status of a *previous request* e.g. `/gpt 255`'
        return_text += '\n>`/gpt [request_id] [your_next_prompt]` will continue a *previous dialogue* e.g. `/gpt 255 now give me baking instructions`'
        return_text += '\n>`/gpt share [request_id] with [slack_user]` will share your request with another user e.g. `/gpt share 255 with firstname.lastname`'
        return_text += '\n>`/gpt list` will list your last 5 request ids'
        return_text += '\n>All GPT responses will be sent in a private message back to the requesting user.'
        return_text += '\n>To post the response publically to the channel where you issued the request, you can click `Post to Channel`.'
        return_text += '\n>For this to work, you must first `/invite @GPT` to the channel'
        count = Job.query.filter_by(state='queued').count()
        return_text += '\nMax prompt length is *3000 characters*'
        return_text += f'\nThere are currently *{count} items* in the queue awaiting processing.'
        return return_text, 200

    if text == 'list':
        return f'Your last 5 request ids: `{get_last_5_requests(user_id)}`', 200

    if len(text) > 3000:
        return 'Prompt too long. Max characters in prompt = 3000', 200

    if len(text) < 20 and text.isdigit():
        int_id = int(text)
        queue = Job.query.filter_by(id=int_id)
        if job := queue.first():

            if job.user_id != user_id and user_id != 'U02B74RS2MT': #user_id != 'U02B74RS2MT' is for debugging purposes -- will be removed
                return f'Naughty naughty! Request `{job.id}` was not your request. You can only query your own requests.\nYour last 5 requests were: `{get_last_5_requests(user_id)}`', 200

            #count = Job.query.filter_by(state='queued').count()
            return responder(job), 200
        else:
            return f'Request: `{text}` not found.', 200

    text_parts = text.split(' ')
    if len(text_parts) > 1:
        if text_parts[0].isdigit():
            prev = Job.query.filter_by(user_id=user_id).order_by(Job.id.desc())

            if job := prev.first():
                if job.user_id != user_id:
                    return f'Naughty naughty! Request `{prev.id}` was not your request. You can only continue your own dialogue.', 200
                else:
                    everything_after_the_request_id = ' '.join(text_parts[1:])
                    text = job.message + '\n' + job.response + '\n' + everything_after_the_request_id
                    text = text[-3000:] if len(text) > 3000 else text

        elif text_parts[0] == 'share':
            if text_parts[1].isdigit() and len(text_parts) > 3 and text_parts[2] == 'with':
                prev = Job.query.filter_by(user_id=user_id, id=int(text_parts[1]))
                if job := prev.first():

                    email_lookup = text_parts[3]

                    if '@vrpconsulting.com' not in email_lookup: email_lookup += '@vrpconsulting.com'

                    headers = {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Authorization": f"Bearer {slack_api_key}"
                    }

                    response = requests.get('https://slack.com/api/users.lookupByEmail',f'email={email_lookup}', headers=headers)

                    if response.status_code == 200 and 'user' in response.json():
                        response_json = response.json()
                        email_lookup_user_id = response_json['user']['id']
                        headers = {
                            "Content-Type": "application/json; charset=utf-8",
                            "Authorization": f"Bearer {slack_api_key}"
                        }
                        payload = {
                            "channel": f"{email_lookup_user_id}",
                            "text": responder(job)
                        }

                        user_response = requests.post('https://slack.com/api/chat.postMessage',json=payload, headers=headers)
                        if user_response.status_code == 200:
                            return f'Request `{job.id}` sent as private message to <@{email_lookup_user_id}> by the GPT Slack App.', 200
                        else:
                            return f'Request `{job.id}` could not be sent to <@{email_lookup_user_id}>.\nResponse code: `{user_response.status_code}`', 200
                    else:
                        return f'User `{text_parts[3]}` not found. Please use the following syntax: `/gpt share request_id with firstname.lastname`', 200

                else:
                    return f'Request `{text_parts[1]}` not found (or it\'s not yours to share). To list your last 5 requests use: `/gpt list`'

            else:
                return 'Are you trying to share a request? Use syntax: `/gpt share [request_id] with [firstname.lastname]` e.g. `/gpt share 255 with joe.bloggs`'

    ip = request.headers.get("X-Real-Ip", "")
    now = datetime.utcnow().isoformat()
    job_id = f"{ip} {now}"

    if channel_id:
        data = Job(slug=job_id,message=text,channel_id=channel_id,user_id=user_id)
    else:
        data = Job(slug=job_id,message=text,webhook_url=slack_webhook_url,user_id=user_id)
    db.session.add(data)
    db.session.commit()

    #count = Job.query.filter_by(state='queued').count()

    return f'Request placed in queue with id: `{data.id}`.\nTo query the status of this request, type `/gpt {data.id}`', 200

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

#INNER CLASSES

class Job(db.Model):
    __tablename__ = "jobs"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(64), nullable=False)
    webhook_url = db.Column(db.String(500), nullable=True)
    message = db.Column(db.String(65535), nullable=True)
    user_id = db.Column(db.String(20), nullable=True)
    channel_id = db.Column(db.String(20), nullable=True)
    state = db.Column(db.String(10), nullable=False, default="queued")
    result = db.Column(db.Integer, default=0)
    response = db.Column(db.String(65535), nullable=True)