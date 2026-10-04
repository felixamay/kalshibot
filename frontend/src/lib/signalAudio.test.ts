import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ALERT_COOLDOWN_MS, considerYesAlert, enableSignalAudio, playSignalAudio, resetYesAlerts, signalAlertKey } from './signalAudio';

test('sound is unlocked by a user action and deduplicated across card remounts', async () => {
  let tones = 0;
  let resumes = 0;
  class Context {
    state = 'suspended';
    currentTime = 5;
    destination = {};
    async resume() { resumes++; this.state = 'running'; }
    createOscillator() { return {frequency:{value:0}, connect() {}, start() { tones++; }, stop() {}}; }
    createGain() { return {gain:{setValueAtTime() {}, exponentialRampToValueAtTime() {}}, connect() {}}; }
  }
  const original = globalThis.window;
  Object.defineProperty(globalThis, 'window', {value:{AudioContext:Context}, configurable:true});
  try {
    assert.equal(playSignalAudio('new'), false);
    assert.equal(await enableSignalAudio(), true);
    assert.equal(resumes, 1);
    assert.equal(playSignalAudio('new'), true);
    assert.equal(playSignalAudio('new'), false);
    assert.equal(tones, 2);
    assert.equal(playSignalAudio('new', true), true);
    assert.equal(playSignalAudio('new', true), false);
    assert.equal(tones, 3);
    const signal = {signal_id:'same-signal', signal_version:1, signal_type:'BET_NOW', status:'ACTIVE'};
    assert.equal(playSignalAudio(signalAlertKey(signal)), true);
    assert.equal(playSignalAudio(signalAlertKey(signal)), false);
    assert.equal(playSignalAudio(signalAlertKey({...signal, signal_version:2})), true);
    assert.equal(playSignalAudio(signalAlertKey({...signal, signal_type:'STOP_EXIT'})), true);
    const expired = signalAlertKey({...signal, status:'EXPIRED', expiration_reason:'TTL_EXPIRED'});
    assert.equal(playSignalAudio(expired, true), true);
    assert.equal(playSignalAudio(expired, true), false);
    assert.equal(tones, 10);
  } finally {
    Object.defineProperty(globalThis, 'window', {value:original, configurable:true});
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
