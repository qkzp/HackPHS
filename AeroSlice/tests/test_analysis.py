import numpy as np
import pytest
from AeroSlice.analysis import Reading,process_trials,climb_time,summarize,save_csv,read_csv,stiffness,synthetic_readings


def readings():return [Reading(t,d,'1','baseline') for t,d in [(0,800),(100,790),(250,775),(450,755),(600,740)]]


def test_upward_displacement_irregular_velocity():
    trial=process_trials(readings())[0]
    assert np.allclose(trial.displacement_mm,[0,10,25,45,60]);assert np.allclose(trial.velocity_mm_s,100)
    assert climb_time(trial,795,750)==pytest.approx(.45)


def test_duplicates_timeout_range_and_preserved_raw():
    rows=readings()+[Reading(100,700,'1','baseline'),Reading(200,None,'1','baseline',error='TIMEOUT'),Reading(300,9000,'1','baseline')]
    trial=process_trials(rows,100,1000)[0]
    assert len(rows)==8;assert len(trial.time_s)==5;assert any('Duplicate' in x for x in trial.warnings)


def test_gap_not_interpolated():
    t=process_trials([Reading(0,800,'1','baseline'),Reading(10000,200,'1','baseline')])[0]
    assert climb_time(t,700,300,max_gap_s=.5) is None


def test_csv_roundtrip_and_synthetic_labels(tmp_path):
    rows=synthetic_readings();p=tmp_path/'sensor.csv';save_csv(rows,p);again=read_csv(p)
    assert rows==again
    trials=process_trials(again);result=summarize(trials,750,300)
    assert len(trials)==10;assert all(r['n']==5 for r in result['variants'])
    assert all('SYNTHETIC' in t.source for t in trials)
    assert result['comparisons'][0]['conclusion']=='Synthetic demonstration only; no experimental conclusion.'


def test_small_sample_inconclusive():
    a=readings();b=[Reading(r.timestamp_ms,r.distance_mm,r.trial_id,'adaptive') for r in a]
    summary=summarize(process_trials(a+b),795,750)
    assert summary['comparisons'][0]['95_ci_percent'] is None
    assert summary['comparisons'][0]['conclusion'].startswith('Inconclusive')


def test_paired_statistics():
    trials=process_trials(synthetic_readings());r=summarize(trials,750,300,paired=True)
    assert r['comparisons'][0]['method'].startswith('Paired');assert r['comparisons'][0]['95_ci_percent'] is not None


def test_outliers_flagged_not_silently_changed():
    rows=[Reading(i*100,800-i*10+(200 if i==4 else 0),'1','baseline') for i in range(10)]
    a=process_trials(rows)[0];b=process_trials(rows,smooth=True)[0]
    assert np.array_equal(a.raw_distance_mm,a.distance_mm);assert np.array_equal(a.raw_distance_mm,b.raw_distance_mm)
    assert not np.array_equal(b.raw_distance_mm,b.distance_mm)


def test_stiffness_units_and_conditions():
    assert stiffness(10,2,5,'Same beam supports')['stiffness_n_per_mm']==5
    assert stiffness(10,2,5,'Same beam supports')['stiffness_to_mass_n_per_mm_per_g']==1
    with pytest.raises(ValueError):stiffness(10,0,5,'')


def test_bad_csv_timestamp(tmp_path):
    p=tmp_path/'bad.csv';p.write_text('timestamp_ms,distance_mm,trial_id,variant\nnan,100,1,baseline\n')
    with pytest.raises(ValueError,match='row 2'):read_csv(p)


def test_invalid_distance_raw_token_preserved(tmp_path):
    p=tmp_path/'invalid.csv';p.write_text('timestamp_ms,distance_mm,trial_id,variant\n0,nan,1,baseline\n100,-7,1,baseline\n200,300,1,baseline\n300,290,1,baseline\n')
    rows=read_csv(p);assert rows[0].distance_mm is None;assert rows[0].raw_distance=='nan'
    assert rows[1].raw_distance=='-7'
    output=tmp_path/'saved.csv';save_csv(rows,output);assert read_csv(output)==rows
