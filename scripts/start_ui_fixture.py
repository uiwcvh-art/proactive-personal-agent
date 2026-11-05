"""Isolated acceptance server. No provider keys and no production state are loaded."""
from pathlib import Path
import os,sys,time
ROOT=Path(__file__).resolve().parents[1]
if not os.getenv('AGENT_DATA_DIR'): raise SystemExit('Run through run_ui_acceptance.py')
sys.path.insert(0,str(ROOT/'server'))
from main import app
from fastapi import Request
from fastapi.responses import JSONResponse
fixture_offline=False
fixture_phone_calls=0
fixture_phone_images=0
fixture_secure_calls=0
@app.middleware('http')
async def fixture_availability(request,call_next):
    global fixture_secure_calls
    if request.url.path=='/api/secure': fixture_secure_calls+=1
    if fixture_offline and request.url.path=='/api/secure': return JSONResponse({'detail':'fixture offline'},status_code=503)
    return await call_next(request)
@app.post('/_fixture/availability')
async def set_fixture_availability(request:Request):
    global fixture_offline
    fixture_offline=bool((await request.json()).get('offline'))
    return {'offline':fixture_offline}
@app.post('/_fixture/notify')
async def native_notification_fixture():
    marker=app.state.brain.bot_context.set('research')
    try: return await app.state.brain.emit('原生通知回流验收',engine='local_prompt')
    finally: app.state.brain.bot_context.reset(marker)
@app.get('/_fixture/phone_calls')
async def phone_call_count(): return {'calls':fixture_phone_calls,'secure_calls':fixture_secure_calls,'images':fixture_phone_images}
@app.post('/v1/images/generations')
async def local_avatar_model(request:Request):
    from fastapi import HTTPException
    data=await request.json()
    if data.get('model')!='avatar-fixture' or request.headers.get('authorization')!='Bearer fixture-avatar-key':
        raise HTTPException(400,'Only the isolated avatar fixture is accepted here')
    from PIL import Image,ImageDraw
    import base64,io
    image=Image.new('RGB',(512,512),'#131722');draw=ImageDraw.Draw(image)
    draw.ellipse((103,103,409,409),fill='#FF8A3D')
    draw.ellipse((151,141,167,157),fill='#FFDCAB');draw.ellipse((324,330,368,374),fill='#E3712B')
    draw.line((73,382,441,166),fill='#FFE5C3',width=14)
    out=io.BytesIO();image.save(out,'PNG')
    return {'data':[{'b64_json':base64.b64encode(out.getvalue()).decode()}]}
@app.post('/v1/chat/completions')
async def local_phone_model(request:Request):
    global fixture_phone_calls,fixture_phone_images
    data=await request.json()
    if data.get('model')!='root-fixture' or request.headers.get('authorization')!='Bearer fixture-root-key':
        from fastapi import HTTPException
        raise HTTPException(400,'Only the isolated Root fixture model is accepted here')
    fixture_phone_calls+=1
    import asyncio,base64,json
    messages=data.get('messages',[])
    for message in messages:
        if isinstance(message.get('content'),list):
            for item in message['content']:
                if item.get('type')=='image_url':
                    image=base64.b64decode(item['image_url']['url'].split(',',1)[1],validate=True)
                    assert image.startswith(b'\x89PNG\r\n\x1a\n') and len(image)>1000
                    fixture_phone_images+=1
    goals=[m.get('content','') for m in messages if m.get('role')=='user' and isinstance(m.get('content'),str)]
    goal=goals[-1] if goals else ''
    if '[slow-stop]' in goal: await asyncio.sleep(15)
    previous=[m for m in messages if m.get('role')=='tool']
    display=json.loads(previous[0]['content'])['display']['displayId'] if previous else None
    steps=[('phone_status',{}),('phone_launch',{'component':'dev.bubuki.proactive/.DisplayTestActivity'}),('phone_capture',{}),('phone_tap',{'x':270,'y':570}),('phone_capture',{})]
    if '[needs-user]' in goal and not goal.startswith('继续完成'):
        step=('need_user',{'question':'请确认工作区画面，然后继续。'})
    else: step=steps[len(previous)] if len(previous)<len(steps) else None
    if previous and json.loads(previous[-1]['content']).get('error'):
        from fastapi import HTTPException
        raise HTTPException(500,'Actual Root tool failed: '+json.loads(previous[-1]['content'])['error'])
    message={'role':'assistant','content':'Root 独立屏真实工具回环验收通过。'}
    if step:
        name,arguments=step
        if name not in ('phone_status','need_user'): arguments['display_id']=display
        message={'role':'assistant','content':None,'tool_calls':[{'id':'root-'+str(len(previous)),'type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}]}
    return {'id':'root-proof','object':'chat.completion','created':int(time.time()),'model':'root-fixture',
        'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if step else 'stop'}],
        'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
app.state.store.state.update(last_curiosity=time.time(),last_research=time.time(),last_user=time.time())
app.state.store.save()
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=18191,access_log=False)
