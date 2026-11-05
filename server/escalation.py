from __future__ import annotations
import asyncio, smtplib, ssl, time
from email.message import EmailMessage

class Escalation:
    def __init__(self,store,vault,publish):
        self.store,self.vault,self.publish=store,vault,publish
        self.mail_lock=asyncio.Lock()
        self.last_test=0

    async def track(self,message,demo=False):
        s=self.store.state
        s['pending'][message['id']]={'id':message['id'],'text':message['text'][:3000],
            'created_at':time.time(),'stage':1,'last_email':0,'emails':0,'demo':demo,'email_status':'pending'}
        self.store.save()
        await self.publish({'type':'escalation','stage':1,'message':message,'id':message['id'],'demo':demo})

    async def ack(self,ids):
        for mid in ids: self.store.state['pending'].pop(mid,None)
        self.store.save()
        await self.publish({'type':'ack','ids':ids})

    async def snooze(self,minutes=60):
        self.store.state['snooze_until']=time.time()+minutes*60
        self.store.save()
        from vault_manager import now_iso
        description=f'免打扰 {minutes} 分钟' if minutes else '好奇心开启'
        self.vault.write('profile/annoyance_valve.md',f'# 烦人度阀门\n\n状态：{description}\n\n设置于：{now_iso()}\n\n结束时间戳：{self.store.state["snooze_until"]}\n')
        self.vault.log(f'烦人度阀门：{description}。')
        await self.publish({'type':'snooze','until':self.store.state['snooze_until']})
        return {'snooze_until':self.store.state['snooze_until']}

    def _mail(self,text,test=False):
        c=self.store.config.smtp
        if not c.host or not c.recipient or not (c.sender or c.username):
            return {'sent':False,'reason':'尚未配置 SMTP 主机、收件邮箱和发件地址'}
        msg=EmailMessage()
        msg['Subject']=('【共生 · 测试】' if test else '【共生 · 等你拍板】')+'替你准备的成果还没看呢'
        msg['From']=c.sender or c.username
        msg['To']=c.recipient
        msg.set_content(f'喂，你的外部前额叶来敲门啦。\n\n{text}\n\n打开共生 App 查看、回复，或选择「别吵我 1 小时」即可停止催单。\n本项目每个待办最多发送 3 封催单邮件。')
        context=ssl.create_default_context()
        cls=smtplib.SMTP_SSL if c.security=='ssl' else smtplib.SMTP
        kwargs={'context':context} if c.security=='ssl' else {}
        with cls(c.host,c.port,timeout=20,**kwargs) as smtp:
            if c.security=='starttls': smtp.starttls(context=context)
            if c.username: smtp.login(c.username,c.password)
            smtp.send_message(msg)
        return {'sent':True}

    async def mail(self,text,test=False):
        async with self.mail_lock:
            if test and time.time()-self.last_test<30: return {'sent':False,'reason':'测试邮件间隔至少 30 秒'}
            if test: self.last_test=time.time()
            try: return await asyncio.to_thread(self._mail,text,test)
            except Exception as e:
                # Do not expose SMTP credentials or provider response bodies.
                return {'sent':False,'reason':f'SMTP 发送失败：{type(e).__name__}，请核对地址和授权码'}

    async def tick(self,now=None):
        now=now or time.time()
        if self.store.state['snooze_until']>now: return
        c=self.store.config
        for p in list(self.store.state['pending'].values()):
            age=now-p['created_at']
            t2,t3=(4,8) if p['demo'] else (c.email_after_minutes*60,c.overlay_after_minutes*60)
            if age>=t2 and p['emails']<3 and (not p['last_email'] or now-p['last_email']>=900):
                result=await self.mail(p['text'],test=p['demo'])
                if p['id'] not in self.store.state['pending'] or self.store.state['snooze_until']>time.time():
                    continue
                # Retry on a bounded cadence even if SMTP is not configured.
                p['emails']+=1
                p['last_email']=now
                p['email_status']='sent' if result['sent'] else result['reason']
                p['stage']=max(2,p['stage'])
                self.store.save()
                await self.publish({'type':'escalation','stage':2,'id':p['id'],'text':p['text'],'email':result,'demo':p['demo']})
            if age>=t3 and p['stage']<3:
                if p['id'] not in self.store.state['pending']: continue
                p['stage']=3
                self.store.save()
                await self.publish({'type':'escalation','stage':3,'id':p['id'],'text':p['text'],'demo':p['demo']})
