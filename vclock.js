// 🎬 가상 시계 (영상 촬영용, 페이지에 미리 넣는 스크립트): __vt.freeze() 하면 시간이 멈추고 __vt.step(ms) 만큼씩만 흐른다
//  - performance.now · Date.now · requestAnimationFrame · setTimeout/setInterval(멈춘 뒤 만든 것)을 가상 시간으로
//  - 느린 컴퓨터에서도 한 장 = 1/30초로 찍어 영상이 매끄럽다 (실제로는 한 장에 0.2~0.5초 걸려도)
(() => {
  const nPerf = performance.now.bind(performance);
  const nDate = Date.now;
  const nRaf = window.requestAnimationFrame.bind(window);
  const nCaf = window.cancelAnimationFrame.bind(window);
  const nST = window.setTimeout.bind(window);
  const nCT = window.clearTimeout.bind(window);
  const nSI = window.setInterval.bind(window);
  const nCI = window.clearInterval.bind(window);
  let frozen = false;
  let vPerf = 0;
  let dateOff = 0;
  let seq = 1e9;
  const timers = new Map();
  let rafs = new Map();
  const vt = {
    get frozen() {
      return frozen;
    },
    freeze() {
      if (frozen) return;
      vPerf = nPerf();
      dateOff = nDate() - vPerf;
      frozen = true;
    },
    /** 가상 Date.now 를 이 시각(ms)으로 맞춤 — 연출 캐릭터가 지나갈 때를 골라 찍기 */
    setNow(epochMs) {
      dateOff = epochMs - vPerf;
    },
    step(ms) {
      const end = vPerf + ms;
      // 그 사이 시각이 된 타이머부터 차례로
      for (;;) {
        let next = null;
        for (const t of timers.values()) if (t.at <= end && (!next || t.at < next.at)) next = t;
        if (!next) break;
        vPerf = Math.max(vPerf, next.at);
        if (next.every) next.at += Math.max(1, next.every);
        else timers.delete(next.id);
        try {
          next.fn(...next.args);
        } catch (e) {
          console.error(e);
        }
      }
      vPerf = end;
      const run = rafs;
      rafs = new Map();
      for (const cb of run.values()) {
        try {
          cb(vPerf);
        } catch (e) {
          console.error(e);
        }
      }
      return run.size;
    },
  };
  window.__vt = vt;
  performance.now = () => (frozen ? vPerf : nPerf());
  Date.now = () => (frozen ? Math.round(vPerf + dateOff) : nDate());
  window.requestAnimationFrame = (cb) => {
    if (!frozen) return nRaf(cb);
    const id = ++seq;
    rafs.set(id, cb);
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    if (rafs.has(id)) rafs.delete(id);
    else nCaf(id);
  };
  const add = (fn, ms, args, every) => {
    const id = ++seq;
    const d = Math.max(0, Number(ms) || 0);
    timers.set(id, { id, at: vPerf + d, fn: typeof fn === "function" ? fn : () => {}, args, every: every ? d : 0 });
    return id;
  };
  window.setTimeout = (fn, ms, ...args) => (frozen ? add(fn, ms, args, false) : nST(fn, ms, ...args));
  window.setInterval = (fn, ms, ...args) => (frozen ? add(fn, ms, args, true) : nSI(fn, ms, ...args));
  window.clearTimeout = (id) => (timers.has(id) ? timers.delete(id) : nCT(id));
  window.clearInterval = (id) => (timers.has(id) ? timers.delete(id) : nCI(id));
})();
