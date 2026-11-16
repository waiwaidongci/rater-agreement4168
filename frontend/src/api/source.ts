import {request,post,patch} from './client';
export const sourceApi={
 studies:()=>request('/studies'),
 study:(id:number)=>request('/studies/'+id),
 specimens:()=>request('/specimens'),
 votes:()=>request('/votes'),
 create:(data:any,locale='zh-CN')=>post('/studies?locale='+encodeURIComponent(locale),data),
 editVote:(id:number,category:string)=>patch('/votes/'+id,{category}),
 editSpecimen:(id:number,description:string)=>patch('/specimens/'+id,{description}),
 addAlias:(studyId:number,data:{code:string;name:string;locale:string})=>post('/studies/'+studyId+'/aliases',data),
 categories:()=>request('/categories'),
 renameCategory:(code:string,data:{name:string;locale:string})=>post('/categories/'+code+'/rename',data),
 quarantine:(includeResolved=false)=>request('/categories/quarantine?include_resolved='+(includeResolved?'true':'false')),
 resolveQuarantine:(id:number,code:string)=>post('/categories/quarantine/'+id+'/resolve',{code}),
 migrate:()=>post('/categories/migrate',{}),
 verify:()=>request('/categories/verify')
};
