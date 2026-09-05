// Development fixture only. Not imported by or included in the production entry.
import React from 'react';
import { createRoot } from 'react-dom/client';
import SongBrowser from '../ui/src/song-browser/SongBrowser.jsx';
import '../ui/src/styles.css';
const listeners = new Set();
const jobs = [];
let outputDir = 'C:\\Test library';
let feedback = {status:'disconnected',url:'',autoRefresh:false};
const snapshot = () => ({ outputDir, jobs: [...jobs], connection: {status:'connected'}, feedback });
const emit = () => listeners.forEach((listener) => listener(snapshot()));
const rows = [
  {id:'54639',title:'Dynamite',artist:'BTS',album:'BE',creator:'Djpavs',version:'2',tuning:'E Standard',parts:'Lead · Rhythm · Bass · Vocals',host:'mediafire',supported:true},
  {id:'6420',title:'One',artist:'Metallica',album:'…And Justice for All',creator:'Nacholede',version:'1',tuning:'E Standard',parts:'Lead · Rhythm · Bass',host:'google-drive',supported:true},
  {id:'3201',title:'One',artist:'Metallica',album:'S&M',creator:'Baoulettes',version:'1',tuning:'Eb Standard',parts:'Lead · Rhythm · Bass',host:'mega',supported:false}
];
const api = {
  getState: async()=>snapshot(), onState: (callback)=>{listeners.add(callback);return()=>listeners.delete(callback);},
  signIn: async()=>({ok:true}), showBrowser: async()=>({ok:true}), showOutput: async()=>({ok:true}),
  chooseOutput: async()=>({outputDir}),
  search: async({query,page})=>{
    if(query==='error') return {ok:false,error:'Simulated network failure.'};
    if(query==='recovery' && !jobs.length) {
      jobs.push({...rows[0],id:'failed-fixture',chartId:'54639',state:'failed',createdAt:Date.now(),hasCachedInput:true,canRetry:true,error:'Simulated conversion interruption. Your PSARC is available for recovery.'});emit();
    }
    return {status:'ready',results:query==='empty'?[]:rows,hasNext:false,page,total:query==='empty'?0:rows.length};
  },
  enqueue: async({id})=>{ const song=rows.find((row)=>row.id===id);const job={...song,id:'job-'+id,chartId:id,state:'downloading',progress:38,createdAt:Date.now(),message:'Simulated download; no network request.'};jobs.push(job);emit();return job; },
  cancel: async({id})=>{const job=jobs.find((job)=>job.id===id);job.state='cancelled';job.message='Cancelled.';job.canRetry=true;emit();return job;},
  retry: async({id})=>{const job={...jobs.find((job)=>job.id===id),id:'retry-'+Date.now(),state:'completed',inOutputDir:true,hasCachedInput:false,canRetry:false,error:'',message:'Simulated retry succeeded without downloading again.',createdAt:Date.now()};jobs.push(job);emit();return job;},
  clearCache: async({id})=>{const job=jobs.find((job)=>job.id===id);job.hasCachedInput=false;emit();return job;},
  openCached: async()=>({canceled:true}), exportDiagnostics:async()=>({exported:true}),
  connectFeedback:async({url})=>{feedback={url,status:'connected',libraryDir:'C:\\Fixture FeedBack library',message:'Simulated FeedBack connection.',autoRefresh:false};emit();return {ok:true};},
  setAutoRefresh:async({enabled})=>{feedback.autoRefresh=enabled;emit();return {ok:true};},
  useFeedbackFolder:async()=>{outputDir=feedback.libraryDir;emit();return {outputDir};},
  refreshFeedback:async()=>{feedback.message='Simulated library refresh requested.';emit();return {ok:true};}
};
createRoot(document.getElementById('root')).render(<div style={{padding:32,maxWidth:1500,margin:'0 auto'}}>
  <p style={{color:'#ffc977',marginBottom:24}}>UI test fixture — sample results and simulated jobs. No live accounts or downloads.</p>
  <SongBrowser api={api}/>
</div>);
