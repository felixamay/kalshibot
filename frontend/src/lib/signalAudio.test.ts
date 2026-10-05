import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ALERT_COOLDOWN_MS, considerStrategyAlert, considerYesAlert, enableSignalAudio, playSignalAudio, resetSignalPlayback, resetStrategyAlerts, resetYesAlerts, signalAlertKey } from './signalAudio';

test('sound is unlocked by a user action and deduplicated across card remounts', async () => {
  resetSignalPlayback();
  let tones = 0;
  let resumes = 0;
  const pulses: number[][] = [];
  class Context {
    state = 'suspended';
    currentTime = 5;
    destination = {};
    async resume() { resumes++; this.state = 'running'; }
    createOscillator() { return {type:'sine', frequency:{value:0}, connect() {}, start() { tones++; }, stop() {}}; }
    createGain() { return {gain:{setValueAtTime() {}, exponentialRampToValueAtTime() {}}, connect() {}}; }
  }
  const original = globalThis.window;
  const originalNav = globalThis.navigator;
  Object.defineProperty(globalThis, 'window', {value:{AudioContext:Context}, configurable:true});
  Object.defineProperty(globalThis, 'navigator', {value:{vibrate(pattern: number[]) { pulses.push(pattern); return true; }}, configurable:true});
  try {
    assert.equal(playSignalAudio('new'), false);
    assert.equal(await enableSignalAudio(), true);
    assert.equal(resumes, 1);
    assert.equal(playSignalAudio('new'), true);
    assert.equal(playSignalAudio('new'), false);
    assert.equal(tones, 3);
    assert.deepEqual(pulses[0], [35, 25, 35, 25, 70]);
    assert.equal(playSignalAudio('new', true), true);
    assert.equal(playSignalAudio('new', true), false);
    assert.equal(tones, 5);
    assert.deepEqual(pulses[1], [100, 50, 100, 50, 160]);
    const signal = {signal_id:'same-signal', signal_version:1, signal_type:'BET_NOW', status:'ACTIVE'};
    assert.equal(playSignalAudio(signalAlertKey(signal)), true);
    assert.equal(playSignalAudio(signalAlertKey(signal)), false);
    assert.equal(playSignalAudio(signalAlertKey({...signal, signal_version:2})), true);
    assert.equal(playSignalAudio(signalAlertKey({...signal, signal_type:'STOP_EXIT'})), true);
    const expired = signalAlertKey({...signal, status:'EXPIRED', expiration_reason:'TTL_EXPIRED'});
    assert.equal(playSignalAudio(expired, true), true);
    assert.equal(playSignalAudio(expired, true), false);
    assert.equal(tones, 16);
    assert.equal(pulses.length, 6);
  } finally {
    Object.defineProperty(globalThis, 'window', {value:original, configurable:true});
    Object.defineProperty(globalThis, 'navigator', {value:originalNav, configurable:true});
    resetSignalPlayback();
  }
});

test('Safari uses webkit audio and browsers without a motor still play the buzz', async () => {
  resetSignalPlayback();
  let started = 0;
  class Context {
    state = 'suspended';
    currentTime = 0;
    destination = {};
    async resume() { this.state = 'running'; }
    createOscillator() { return {type:'sine', frequency:{value:0}, connect() {}, start() { started++; }, stop() {}}; }
    createGain() { return {gain:{setValueAtTime() {}, exponentialRampToValueAtTime() {}}, connect() {}}; }
  }
  const original = globalThis.window;
  const originalNav = globalThis.navigator;
  Object.defineProperty(globalThis, 'window', {value:{webkitAudioContext:Context}, configurable:true});
  Object.defineProperty(globalThis, 'navigator', {value:{}, configurable:true});
  try {
    assert.equal(await enableSignalAudio(), true);
    assert.equal(playSignalAudio('webkit'), true);
    assert.equal(started, 3);
  } finally {
    Object.defineProperty(globalThis, 'window', {value:original, configurable:true});
    Object.defineProperty(globalThis, 'navigator', {value:originalNav, configurable:true});
    resetSignalPlayback();
  }
});

test('a browser without Web Audio still plays the signal buzz', async () => {
  resetSignalPlayback();
  let plays = 0;
  class FakeAudio {
    muted = false;
    currentTime = 0;
    src = '';
    constructor(src: string) { this.src = src; }
    async play() { plays++; }
    pause() {}
  }
  const original = globalThis.window;
  const originalAudio = globalThis.Audio;
  const originalNav = globalThis.navigator;
  Object.defineProperty(globalThis, 'window', {value:{}, configurable:true});
  Object.defineProperty(globalThis, 'Audio', {value:FakeAudio, configurable:true});
  Object.defineProperty(globalThis, 'navigator', {
    value:{vibrate() { throw new Error('blocked'); }},
    configurable:true,
  });
  try {
    assert.equal(await enableSignalAudio(), true);
    assert.equal(plays, 1);
    assert.equal(playSignalAudio('html'), true);
    assert.equal(playSignalAudio('html'), false);
    assert.equal(plays, 2);
  } finally {
    Object.defineProperty(globalThis, 'window', {value:original, configurable:true});
    Object.defineProperty(globalThis, 'Audio', {value:originalAudio, configurable:true});
    Object.defineProperty(globalThis, 'navigator', {value:originalNav, configurable:true});
    resetSignalPlayback();
  }
});

test('YES sound plays immediately, then only after a real re-trigger and 20 seconds', () => {
  resetYesAlerts();
  assert.equal(ALERT_COOLDOWN_MS, 20000);
  assert.equal(considerYesAlert('m|Ann|YES', true, 1_000), true);
  assert.equal(considerYesAlert('m|Ann|YES', true, 5_000), false);
  assert.equal(considerYesAlert('m|Ann|YES', true, 22_000), false);
  assert.equal(considerYesAlert('m|Bea|YES', true, 6_000), true);
  assert.equal(considerYesAlert('m|Bea|YES', false, 7_000), false);
  assert.equal(considerYesAlert('m|Bea|YES', true, 8_000), false);
  assert.equal(considerYesAlert('m|Bea|YES', false, 9_000), false);
  assert.equal(considerYesAlert('m|Bea|YES', true, 6_000 + ALERT_COOLDOWN_MS), true);
});

test('strategy alerts play immediately and then wait 20 seconds', () => {
  resetStrategyAlerts();
  assert.equal(considerStrategyAlert('KX|TAKE PROFIT TRIGGERED', 1_000), true);
  assert.equal(considerStrategyAlert('KX|TAKE PROFIT TRIGGERED', 2_000), false);
  assert.equal(considerStrategyAlert('KX|STOP LOSS TRIGGERED', 2_000), true);
  assert.equal(considerStrategyAlert('KX|TAKE PROFIT TRIGGERED', 1_000 + ALERT_COOLDOWN_MS), true);
});
