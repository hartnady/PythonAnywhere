import requests, openai, traceback
from time import sleep

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import SQLALCHEMY_DATABASE_URI, SQLALCHEMY_ENGINE_OPTIONS, OPEN_AI_KEY, SLACK_API_KEY
from flask_app import Job

openai.api_key = OPEN_AI_KEY

engine = create_engine(
    SQLALCHEMY_DATABASE_URI, **SQLALCHEMY_ENGINE_OPTIONS
)
Session = sessionmaker(engine)

def gpt_max(prompt):
    params = {
        "prompt":prompt,
        "engine":"text-davinci-003",
        "temperature":0.4,
        "max_tokens":4097-len(prompt)-100
    }
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

def find_pending_job():
    with Session.begin() as session:
        queue = session.query(Job).filter_by(state="queued")
        if job := queue.first():
            job.state = "processing"
            return job.id

def process_job(job_id):
    print(f"Processing job: {job_id}...", end=" ", flush=True)

    with Session.begin() as session:
        job = session.query(Job).filter_by(id=job_id).first()
        gpt_resp = gpt_max(job.message)

        gpt_resp = gpt_resp.replace('\n','\n>')

        payload = {
            "channel": f"{job.user_id}",
            "blocks": [
        		{
        			"type": "section",
        			"text": {
        				"type": "mrkdwn",
        				"text": f"<@{job.user_id}> asked: {job.message}\n>{gpt_resp}"
        			},
        			"accessory": {
        				"type": "button",
        				"text": {
        					"type": "plain_text",
        					"text": "Post to GPT Channel"
        				},
        				"value": "click_me_123",
        				"action_id": "button-action"
        			}
        		}
        	]
        }

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": "Bearer " + SLACK_API_KEY
        }

        response = requests.post('https://slack.com/api/chat.postMessage', json=payload, headers=headers)

        if response.status_code == 200:

            with Session.begin() as session:
                session.query(Job).filter_by(id=job_id).update(
                    {"result": 1, "state": "completed", "response": gpt_resp}
                )

            print(f"{job_id} is complete.")

        else:

            with Session.begin() as session:
                session.query(Job).filter_by(id=job_id).update(
                    {"result": 0, "state": "failed"}
                )

            print(f"{job_id} failed to process.")
            print(f"{response.content}")



if __name__ == "__main__":
    while True:
        if job_id := find_pending_job():
            process_job(job_id)
        else:
            sleep(1)
