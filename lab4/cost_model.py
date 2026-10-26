"""Lab4 design estimate, not a deployed system or a billing statement.
Run: python3 lab4/cost_model.py. No network requests, credentials or cloud changes.
"""
import json
from pathlib import Path
from math import ceil
ROOT = Path(__file__).resolve().parent
GiB = 2**30
HOURS = 720  # 30 days; hourly storage rates are converted explicitly
RATES = {
    'run_cpu_s': .000024, 'run_memory_gib_s': .0000025,
    'run_idle_cpu_s': .0000025, 'run_request': .40/1e6,
    'llm_input_token': .25/1e6, 'llm_output_token': 1.50/1e6,
    'gcs_gib_h': .000027397, 'gcs_A': .005/1000, 'gcs_B': .0004/1000,
    'firestore_read': .03/100000, 'firestore_write': .09/100000,
    'firestore_delete': .01/100000, 'firestore_gib_h': .000205479,
    'firestore_backup_gib_h': .000041096,
    'tasks_op': .40/1e6, 'egress_gib': .12, 'logging_gib': .50,
    'registry_gib_h': .000136986, 'build_minute': .006,
}
STAGES = [
    dict(id='initial', name='Начальное', users=100, jobs=1000, peak_jobs_s=.1,
         web_min=0, web_max=3, worker_max=5, task_dispatch_s=.25,
         registry_gib=1, build_minutes=100, staging_factor=0, pitr=False, ops_hours=2),
    dict(id='partners', name='Тестирование партнёрами', users=2000, jobs=50000, peak_jobs_s=2,
         web_min=0, web_max=20, worker_max=40, task_dispatch_s=3,
         registry_gib=3, build_minutes=500, staging_factor=.10, pitr=False, ops_hours=6),
    dict(id='production', name='Продовое решение', users=20000, jobs=1000000, peak_jobs_s=20,
         web_min=1, web_max=50, worker_max=250, task_dispatch_s=21,
         registry_gib=10, build_minutes=2000, staging_factor=.10, pitr=True, ops_hours=16),
]

def estimate(stage, input_tokens=4000, output_tokens=500, worker_seconds=10, attempts=1.05):
    n = stage['jobs']; m = n * attempts; r = RATES
    fs_gib = n * 4*1024 / GiB  # metadata + indexes; steady-state 30-day retention
    gcs_gib = n * 72*1024 / GiB * (37/30)  # input + result, plus seven-day soft delete
    egress_gib = (stage['users'] * 20*2**20 + n*28*1024) / GiB
    web_requests = 10*n + 50*stage['users']  # API + 5 SPA assets per visit, 10 visits/MAU
    web_seconds = n + .01*50*stage['users']
    logs_gib = (web_requests+m)*10*1024 / GiB
    costs = {
        'AI tokens': m*(input_tokens*r['llm_input_token']+output_tokens*r['llm_output_token']),
        'Cloud Run web active': web_seconds*(r['run_cpu_s']+.5*r['run_memory_gib_s'])+web_requests*r['run_request'],
        'Cloud Run worker': m*worker_seconds*(r['run_cpu_s']+r['run_memory_gib_s'])+m*r['run_request'],
        'Cloud Tasks': (n+m)*r['tasks_op'],
        'Cloud Storage': gcs_gib*HOURS*r['gcs_gib_h']+(n+m)*r['gcs_A']+4*m*r['gcs_B'],
        'Firestore': 10*m*r['firestore_read']+3*m*r['firestore_write']+n*r['firestore_delete']+fs_gib*HOURS*r['firestore_gib_h'],
        'Internet egress': egress_gib*r['egress_gib'],
        'Cloud Logging': logs_gib*r['logging_gib'],
    }
    # Free allowances are deliberately not subtracted: shared billing-account quotas.
    # Identity Platform Tier1 email/social: 0 USD while MAU <= 50,000; no SMS/SAML.
    costs['Identity Platform'] = max(0,stage['users']-50000)*.0055
    variable = sum(costs.values())
    costs['Staging allowance'] = variable*stage['staging_factor']
    # Full-month idle floor added on top of active estimates: conservative overestimate.
    costs['Web minimum instance allowance'] = stage['web_min']*HOURS*3600*(r['run_idle_cpu_s']+.5*r['run_memory_gib_s'])
    costs['Firestore PITR + 7 daily backups'] = (fs_gib*HOURS*(r['firestore_gib_h']+7*r['firestore_backup_gib_h'])) if stage['pitr'] else 0
    costs['Artifact Registry'] = stage['registry_gib']*HOURS*r['registry_gib_h']
    costs['Cloud Build'] = stage['build_minutes']*r['build_minute']
    subtotal = sum(costs.values()); reserve = .20*subtotal
    return dict(stage=stage, volumes=dict(attempts=m,web_requests=web_requests,web_seconds=web_seconds,gcs_gib=gcs_gib,firestore_gib=fs_gib,egress_gib=egress_gib,logs_gib=logs_gib),
                costs=costs, subtotal=subtotal,reserve=reserve,budget=subtotal+reserve,
                cost_per_job=(subtotal+reserve)/n, ops_labor=stage['ops_hours']*15,
                tco=subtotal+reserve+stage['ops_hours']*15,
                peak_worker_instances=ceil(stage['peak_jobs_s']*attempts*worker_seconds))

if __name__ == '__main__':
    results=[estimate(s) for s in STAGES]
    sensitivity=[dict(case=name,**estimate(STAGES[-1],**kw)) for name,kw in [
        ('baseline',{}),('double_input',{'input_tokens':8000}),
        ('double_output',{'output_tokens':1000}),('worker_30s',{'worker_seconds':30}),
        ('attempts_1.20',{'attempts':1.20})]]
    output=dict(checked_on='2026-10-26',currency='USD',month_hours=HOURS,rates=RATES,results=results,sensitivity=sensitivity)
    (ROOT/'cost_results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    for x in results: print(x['stage']['id'],f"cloud ${x['subtotal']:.2f}; +20% ${x['budget']:.2f}; per job ${x['cost_per_job']:.6f}; TCO ${x['tco']:.2f}")
