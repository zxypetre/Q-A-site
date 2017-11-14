from flask import (Flask,render_template,request,redirect,make_response)
import pymongo
from pymongo import MongoClient
from datetime import datetime, timedelta
from bson import objectid
import markdown
import json
import urllib.parse
import os
import requests
import uuid
import redis
import time
from settings import AUTH, MONGO, REDIRECT_HOST, REDIS

rdb = redis.Redis(host=REDIS['host'],port=REDIS['port'],db=REDIS['db'])
redirect_uri = REDIRECT_HOST+'/callback'
client_id = AUTH['github']['client_id']
client_secret = AUTH['github']['secret']

mongo = MongoClient(host=MONGO['host'],port=MONGO['port'])
dbposts = mongo.posts
dbanswers = mongo.answers
dbusers = mongo.users

app=Flask(__name__)

@app.route('/', methods=['GET'])
@app.route('/tag/<tag>', methods=['GET'])
def changepage(tag=None): 
    flt = {}
    if tag:
        flt = {'tags': tag} 
    page = request.values.get('page','1')
    try:
        page = int(page)
    except ValueError:
        page = 1
    if page > 3:
        page = 3
    start = (page-1)*10
    limits = {i: 1 for i in ('content', 'creator', 'tags', 'title',
            'lastModified')}
    print(limits)
    r = dbposts.posts.find(flt, limits).sort([('lastModified',-1)]).skip(start).limit(10)
    questions = [i for i in r if i]
    print(r,questions)
    return render_template('index.html',out=questions)

@app.route('/auth/github')
def githublogin():
    return redirect('https://github.com/login/oauth/authorize?redirect_url='+redirect_uri+'&response_type=code&client_id='+client_id)

@app.route('/callback', methods=['GET'])
def login():
    code = request.values.get('code')
    access_token = requests.get('https://github.com/login/oauth/access_token?client_id='+client_id+'&client_secret='+client_secret+'&code=%s&redirect_url='+redirect_uri%(code)).content.decode("utf-8")
    api = requests.get('https://api.github.com/user?%s'%(access_token))
    if api:
        api = json.loads(api.text)
        username = api['login']
        uid = api['id']
        print(username,uid)
        r = dbusers.users.find_one({'uid':uid})
        if r:
            resp=make_response(redirect('/'))
            uuid0 = str(uuid.uuid4())
            rdb.set(uuid0,uid,ex=3600*24*30)
            resp.set_cookie('username',uuid0,expires=time.time()+3600*24*30)
            return resp
        else:
            dbusers.users.insert({'authdomain':'github','uid':uid,'username':username})
            resp=make_response(redirect('/'))
            uuid1 = str(uuid.uuid4())
            rdb.set(uuid1,uid,ex=3600*24*30)
            resp.set_cookie('username',uuid1,expires=time.time()+3600*24*30)
            return resp 
    else:
        return redirect('/')

@app.route('/user/<user>', methods=['GET'])
def answers(user):
    if '_' not in user:
        return redirect('/')
    domain, uid = user.split('_', 1)
    items={}

    #get all answers answered by someone
    r = dbanswers.answers.find({'creator': {'$all': [domain, uid]}}).sort([('lastModified', -1)]).limit(10)
    #get all posts against the answers
    tmp = [i.get('post_id', ' ') for i in r]
    posts = dbposts.posts.find({'_id': {'$in': tmp}},
                sort=[('lastModified', -1)])
    items['answers'] = [i for i in posts if i]

    posts = dbposts.posts.find({'creator': {'$all': [domain, uid]}},
                sort=[('lastModified', -1)], limit=10)
    items['questions'] = [i for i in posts if i]
    print(domain, uid,r,tmp,posts,items)
    return render_template('user.html', items = items)


@app.route('/ask', methods=['GET'])
def ask():
    return render_template('ask.html')

@app.route('/edit/p/<edit>', methods=['GET'])
def initialize(edit):
    return edit


def mongo_check_id(_id):
    if _id and objectid.ObjectId.is_valid(_id):
        return objectid.ObjectId(_id)

@app.route('/p/<pageid>', methods=['GET'])
def getpageid(pageid):
        _id = mongo_check_id(pageid)
        #create_time = _id.generation_time
        question = dbposts.posts.find_one({'_id': _id})
        if not question:
            return redirect('/')
        question['content'] = markdown.markdown(question.get('content', ''))
        cursor = dbanswers.answers.find({'post_id': _id})
        answers = []
        for doc in cursor:
            doc['content'] = markdown.markdown(doc.get('content', ''))
            answers.append(doc)
        return render_template('question.html', out=question, answers=answers)

@app.route('/about', methods=['GET'])
def geturl():
        post = dbposts.posts.find_one({'linkurl': '/about'})
        if post:
            _id = str(post.get('_id', ''))
            return redirect('/p/'+_id),301
        else:
            return redirect('/')


def write_result(result, ok={'msg':'OK'}, fail={'msg':'FAIL'}):
        if result:
            ok['result'] = 1
            return json.dumps(ok)
        else:
            fail['result'] = -1
            return json.dumps(fail)

def current():
    uuid = request.cookies.get('username')
    uid = int(rdb.get(uuid).decode("utf-8"))
    print(uuid,uid)
    if uid:
        r = dbusers.users.find_one({"uid":uid})
        domain = r['authdomain']
        username = r['username']
        return [domain,uid,username]

@app.route('/ajax/post-question', methods=['POST'])
def postquestion():
        title = request.values.get('title', None)
        content = request.values.get('content', None)
        editid = request.values.get('editid', '')
        current_user = current()
        #valid editid means  user is editing the page but not create new page
        if editid:
            _id = mongo_check_id(editid)
            #check if user if the owner of this editing page
            q = dbposts.posts.find_one({'_id': _id}, {'creator': 1})
            if (not q) and current_user[:2] != tuple(q['creator'])[:2]:
                #TODO XXX: currently only owner can edit his post
                #in the future maybe more users can edit it (depend on privilige)
                return write_result(False, fail={'msg': 'fail to update!'})
        else:
            _id = objectid.ObjectId()       #buscreate new page
        if title and content:
            post = dict(title=title, content=content)
            tags = request.values.get('tags', '').split(',')
            post['tags'] = [tag.strip() for tag in tags]
            post['creator'] = current_user
            r = dbposts.posts.update_one({'_id': _id},
                    {'$set': post, '$push': {'history': post},
                        '$currentDate': {'lastModified': True}},
                upsert=True)
            if r.upserted_id or r.modified_count:
                return write_result(True, ok={'pageid': str(_id)})
            return write_result(False, fail={'msg': 'fail to insert!'})
        else:
            return write_result(False, fail={'msg': 'title and content should ' +
                'be empty!'})
            

@app.route('/ajax/post-answer', methods=['POST'])
def postanswer():
        pathname = request.values.get('pathname', '/p/')
        pathname = pathname[3:]             #skip the /p/ in the head
        post_id = mongo_check_id(pathname)
        content = request.values.get('content', None)
        answerid = request.values.get('answerid', None)
        print(pathname,content,answerid)
        current_user = current()
        if not content:
            return write_result(False, fail={'msg': 'invalid content!'})
        answer = dict(content=content, post_id=post_id)
        answer['creator'] = current_user
        #if has answerid, it's a edited answer otherwise a new answer
        #for a edited answer, need to check the creator
        if answerid: 
            answerid = mongo_check_id(answerid)
            r = dbanswers.answers.update_one(
                    {'_id': answerid, 'creator': list(current_user)},
                    {'$set': answer, '$push': {'history': answer},
                        '$currentDate': {'lastModified': True}})
            return write_result(r.modified_count)
        else:
            answer['lastModified'] = datetime.now()
            r = dbanswers.answers.insert_one(answer)
            _id = r.inserted_id
            return write_result(_id)


@app.route('/ajax/edit-answer', methods=['GET'])
def getanswer():
        _id = request.values.get('_id', '')
        _id = mongo_check_id(_id)
        limits = {i: 1 for i in ('content', 'creator')}
        answer = dbanswers.answers.find_one({'_id': _id}, limits)
        current_user = current()
        if not answer:
            return write_result(None, fail={'msg': 'cannot find this answer!'})
        #TODO XXX: currently only owner can edit his post
        #in the future maybe more users can edit it (depend on privilige)
        creator = answer.get('creator', '')
        if len(creator) > 1 and current_user[:2] != creator[:2]:
            return write_result(None, fail={'msg': 'you arenot the creator!'})
        answer.pop('_id')
        return json.dumps(answer)

@app.route('/ajax/edit-question', methods=['GET'])
def getquestion():
        editid = request.values.get('editid', '')
        _id = mongo_check_id(editid)
        limits = {i: 1 for i in ('content', 'creator', 'tags', 'title')}
        question = dbposts.posts.find_one({'_id': _id}, limits)
        current_user = current()
        if question:
            #TODO XXX: currently only owner can edit his post
            #in the future maybe more users can edit it (depend on privilige)
            if current_user[:2] == tuple(question['creator'])[:2]:
                question.pop('_id')
                return write(json.dumps(question))

@app.route('/ajax/post-comment', methods=['POST'])
def postcomment():
    _id = request.values.get('pathname', '/p/')[3:]
    _id = mongo_check_id(_id)
    content = request.values.get('content', None)
    current_user = current()
    if content:
        comment = dict(content=content)
        comment['creator'] = current_user
        comment['time'] = datetime.now()
        #if answerid exists, then it's a answer comment
        answerid = request.values.get('answerid', None)
        if answerid:
            answerid = mongo_check_id(answerid)
            r = dbanswers.answers.update_one({'_id': answerid},
                    {'$inc':{'commentCount':1}, '$push':{'comments':comment}})
            return write_result(r.modified_count, ok={'pageid': str(_id)})

        else:
            r = dbposts.posts.update_one({'_id': _id},
                    {'$inc': {'commentCount': 1}, '$push': {'comments': comment}})
            return write_result(r.modified_count, ok={'pageid': str(_id)})
            

@app.route('/ajax/vote', methods=['GET'])
def getvote():
        _id = request.values.get('_id', None)
        content = request.values.get('content', None)
        vote = request.values.get('vote', None)
        _id = mongo_check_id(_id)
        current_user = current()
        if content and vote:
            if vote == 'up':
                voteresult = 'up'
            elif vote == 'down':
                voteresult = 'up'
            else:
                return write_result(None)
            domain, uid = current_user[:2]
            user = dict(authdomain=domain, uid=int(uid))
            #the user document will look like:
            #{authdomain: baidu, uid:XXX,
            #    votes: {question_id_1:true,    //true means vote up
            #            question_id_2:false}   //false means vote down
            #}
            #TODO XXX: need to think about a user vote up then vote down
            r = dbusers.users.update_one(user,
                    {'$set':{'votes.'+str(_id): voteresult}})
            print(r.matched_count, r.modified_count,user)
            #if document not modified, that means user already voted
            #then needn't to inc/dec the voteCount
            if r.modified_count:
                #increase/decrease the vote count of question/answer
                #TODO XXX: this should not be done here
                #should be send to a task queue and let the queue write to db
                inc = 1 if vote=='up' else -1
                if content == 'answer':
                    dbanswers.answers.update_one({'_id':_id},
                            {'$inc': {'voteCount': inc}})
                if content == 'question':
                    dbposts.posts.update_one({'_id':_id},
                            {'$inc': {'voteCount': inc}})
            return write_result(r.modified_count)

if __name__ == '__main__':
    app.run(debug=True)