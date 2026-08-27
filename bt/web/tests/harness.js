/*
 * index.html 의 <script> 를 Node 에서 그대로 실행하기 위한 최소 DOM 스텁.
 *
 * 이 앱은 빌드가 없는 바닐라 JS 라서, 테스트하려면 실행 환경만 흉내 내면 된다.
 * 브라우저·jsdom 없이 필요한 것만 채웠다 — 여기서 잡으려는 버그는 렌더링이
 * 아니라 "연결 상태에 따라 버튼과 화면이 맞게 바뀌는가" 이기 때문이다.
 * (실제로 이 스텁으로 '취소 후 버튼이 영영 잠기는' 버그를 잡았다.)
 */
const fs = require("fs");
const path = require("path");

const INDEX = path.join(__dirname, "..", "index.html");

const NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";
const NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";

function mkEl(id) {
  const el = {
    id, textContent: "", value: "", hidden: false,
    disabled: false, checked: false, dataset: {}, style: {},
    className: "", classes: new Set(), children: [], handlers: {},
    classList: {
      add: (c) => el.classes.add(c),
      remove: (c) => el.classes.delete(c),
      toggle: (c, on) => (on ? el.classes.add(c) : el.classes.delete(c)),
      contains: (c) => el.classes.has(c),
    },
    addEventListener: (ev, fn) => { (el.handlers[ev] ||= []).push(fn); },
    removeEventListener: () => {},
    appendChild: (c) => { el.children.push(c); return c; },
    remove: () => {}, focus: () => {}, scrollIntoView: () => {},
    querySelectorAll: () => [],
    click: () => (el.handlers.click || []).forEach((f) => f()),
  };
  // innerHTML = "" 는 이 앱에서 '자식 비우기' 로 쓰인다.
  Object.defineProperty(el, "innerHTML", {
    get: () => el._html || "",
    set: (v) => { el._html = v; if (v === "") el.children.length = 0; },
  });
  return el;
}

/** 정상 동작하는 장비. dev.notify(obj) 로 장비 → 호스트 메시지를 흘려보낸다. */
function makeLiveDevice(id, name) {
  let notifyHandler = null;
  const rx = { writeValueWithResponse: async () => {}, writeValueWithoutResponse: async () => {} };
  const tx = {
    startNotifications: async () => {},
    addEventListener: (ev, fn) => { if (ev === "characteristicvaluechanged") notifyHandler = fn; },
  };
  const server = {
    getPrimaryService: async () => ({
      getCharacteristic: async (u) => (u === NUS_RX ? rx : u === NUS_TX ? tx : null),
    }),
  };
  const dev = {
    id, name, handlers: {},
    gatt: {
      connected: false,
      connect: async () => { dev.gatt.connected = true; return server; },
      disconnect() {
        if (!dev.gatt.connected) return;
        dev.gatt.connected = false;
        (dev.handlers.gattserverdisconnected || []).forEach((f) => f());
      },
    },
    addEventListener: (ev, fn) => { (dev.handlers[ev] ||= []).push(fn); },
    removeEventListener: () => {},
    notify: (obj) => notifyHandler({
      target: { value: new TextEncoder().encode(JSON.stringify(obj) + "\n") },
    }),
    drop: () => {   // 갑작스런 끊김(장비 전원 차단 등)
      dev.gatt.connected = false;
      (dev.handlers.gattserverdisconnected || []).forEach((f) => f());
    },
  };
  return dev;
}

/** 꺼져 있는 장비 — gatt.connect() 가 영영 끝나지 않는다. */
function makeDeadDevice(id, name) {
  const dev = {
    id, name, handlers: {},
    gatt: { connected: false, connect: () => new Promise(() => {}), disconnect() {} },
    addEventListener: (ev, fn) => { (dev.handlers[ev] ||= []).push(fn); },
    removeEventListener: () => {},
  };
  return dev;
}

/** 연결은 되는데 GATT 준비에서 실패하는 장비(연결 중 링크가 끊기는 상황). */
function makeFlakyDevice(id, name) {
  const dev = {
    id, name, handlers: {}, connects: 0,
    gatt: {
      connected: false,
      connect: async () => {
        dev.connects++;
        dev.gatt.connected = true;
        return { getPrimaryService: async () => { throw new Error("서비스 검색 실패"); } };
      },
      disconnect() {
        if (!dev.gatt.connected) return;
        dev.gatt.connected = false;
        (dev.handlers.gattserverdisconnected || []).forEach((f) => f());
      },
    },
    addEventListener: (ev, fn) => { (dev.handlers[ev] ||= []).push(fn); },
    removeEventListener: () => {},
  };
  return dev;
}

/**
 * index.html 의 스크립트를 실행하고 조작 수단을 돌려준다.
 * devices: getDevices() 가 돌려줄 장비들. pick: requestDevice() 가 고를 장비.
 */
function loadApp({ devices = [], pick = null } = {}) {
  const els = new Map();
  const doc = {
    hidden: false, handlers: {},
    getElementById: (id) => {
      if (!els.has(id)) els.set(id, mkEl(id));
      return els.get(id);
    },
    createElement: () => mkEl("new"),
    addEventListener: (ev, fn) => { (doc.handlers[ev] ||= []).push(fn); },
    querySelectorAll: () => [],
  };
  const win = {
    handlers: {},
    addEventListener: (ev, fn) => { (win.handlers[ev] ||= []).push(fn); },
    scrollTo: () => {}, isSecureContext: true,
  };

  global.document = doc;
  global.window = win;
  // Node 의 navigator 는 읽기 전용 전역이라 defineProperty 로 덮어써야 한다.
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      bluetooth: {
        getDevices: async () => devices,
        requestDevice: async () => {
          if (!pick) throw new Error("사용자가 선택창을 닫음");
          return pick;
        },
      },
      // serviceWorker 키는 두지 않는다("serviceWorker" in navigator → false)
    },
  });
  const store = {};
  global.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  global.caches = { keys: async () => [] };
  global.location = { reload() {} };

  // 실제 HTML 의 버튼 라벨(setBusyBtn 이 원래 라벨을 기억해 되돌린다)
  doc.getElementById("disconnect").textContent = "끊기";
  doc.getElementById("findNew").textContent = "+ 새 장비 찾기";

  const code = fs.readFileSync(INDEX, "utf8").match(/<script>([\s\S]*?)<\/script>/)[1];
  new Function(code)();

  const $ = (id) => doc.getElementById(id);
  return {
    $, doc, win,
    tick: (ms = 40) => new Promise((r) => setTimeout(r, ms)),
    load: async () => { for (const f of win.handlers.load || []) await f(); },
    cards: () => $("devs").children.filter((c) => c.handlers.click),
    screen: () =>
      (!$("screen-devices").hidden && "장비목록")
      || (!$("screen-main").hidden && "설정")
      || (!$("screen-psk").hidden && "비밀번호") || "?",
  };
}

module.exports = { loadApp, makeLiveDevice, makeDeadDevice, makeFlakyDevice };
