from flask import Flask, request, abort
from io import BytesIO, StringIO
import PyPDF2
from pdfminer.high_level import extract_text_to_fp
import pdfminer.layout
import openai, transformers, json, traceback, requests
openai.api_key = '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'

app = Flask(__name__)
app.config['DEBUG'] = True

def token_count(prompt):
    tokenizer = transformers.AutoTokenizer.from_pretrained("openai-gpt")
    tokens = tokenizer.tokenize(prompt)
    return len(tokens)+500 #margin of error

def Embedding(content, engine='text-similarity-ada-001'):
    content = content.encode(encoding='ASCII',errors='ignore').decode()
    response = openai.Embedding.create(input=content,engine=engine)
    vector = response['data'][0]['embedding']
    return vector

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

def gpt_complete(prompt):
    params = {
        "prompt":prompt,
        "engine":"text-davinci-003",
        "temperature":0.1,
        "max_tokens":4097-token_count(prompt)
    }
    response = openai.Completion.create(**params)
    return response.choices[0].text

@app.route('/', methods=['GET'])
def root():
    return 'Welcome to PDF Extract. Please use HTTP POST to submit PDF data for conversion to a JSON-L document.'


@app.route('/', methods=['POST'])
def extract_pdfminer_text():
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

@app.route('/pypdf2', methods=['POST'])
def extract_pypdf2_text():
    # Get the binary data from the POST request
    pdf_data = request.data

    # Create a PyPDF reader object from the binary data
    pdf_reader = PyPDF2.PdfFileReader(BytesIO(pdf_data))

    # Extract the text from the PDF and return it
    text = ''
    for page_num in range(pdf_reader.getNumPages()):
        page = pdf_reader.getPage(page_num)
        text += page.extractText()

    return text, 200, {'Content-Type': 'text/plain'}


if __name__ == '__main__':
    app.run()
