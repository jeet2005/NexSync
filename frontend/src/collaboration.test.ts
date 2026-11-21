import {describe,expect,it} from 'vitest';
import * as Y from 'yjs';
import {canApplySuggestion} from './conflict';

function stateWith(edits: Uint8Array[][]) {
  const target=new Y.Doc();
  for(const updates of edits) for(const update of updates) Y.applyUpdate(target,update);
  return target.getText('content').toString();
}

describe('Yjs collaboration convergence',()=>{
  it('merges simultaneous insertions in either delivery order',()=>{
    const a=new Y.Doc(),b=new Y.Doc();
    a.getText('content').insert(0,'A');b.getText('content').insert(0,'B');
    const ua=Y.encodeStateAsUpdate(a),ub=Y.encodeStateAsUpdate(b);
    expect(stateWith([[ua,ub]])).toBe(stateWith([[ub,ua]]));
  });
  it('handles duplicates and replay after reconnect idempotently',()=>{
    const a=new Y.Doc();a.getText('content').insert(0,'persistent');
    const update=Y.encodeStateAsUpdate(a);
    expect(stateWith([[update,update]])).toBe('persistent');
  });
  it('preserves simultaneous inserts and deletes after out-of-order delivery',()=>{
    const seed=new Y.Doc();seed.getText('content').insert(0,'abcd');
    const init=Y.encodeStateAsUpdate(seed);
    const a=new Y.Doc(),b=new Y.Doc();Y.applyUpdate(a,init);Y.applyUpdate(b,init);
    a.getText('content').delete(1,1);b.getText('content').insert(2,'X');
    const ua=Y.encodeStateAsUpdate(a,init),ub=Y.encodeStateAsUpdate(b,init);
    expect(stateWith([[init,ua,ub]])).toBe(stateWith([[ub,ua,init]]));
  });
});

describe('AI stale result guard',()=>{
  it('rejects an AI replacement when a human changed the source during generation',()=>{
    const captured='Original content';
    const human='Human’s updated content';
    const ai='AI-generated replacement';
    expect(canApplySuggestion(captured,human)).toBe(false);
    expect(human).not.toBe(ai);
  });
  it('allows a proposal if the source has not changed',()=>{
    expect(canApplySuggestion('Original content','Original content')).toBe(true);
  });
});
