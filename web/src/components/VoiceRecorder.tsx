import { useEffect, useRef, useState } from "react";

function encodeWav(chunks: Float32Array[], sampleRate: number): Blob {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const samples = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) { samples.set(chunk, offset); offset += chunk.length; }

  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (at: number, value: string) => {
    for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i));
  };
  text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE"); text(12, "fmt "); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, samples.length * 2, true);
  let cursor = 44;
  for (const sample of samples) {
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(cursor, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    cursor += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export default function VoiceRecorder({ disabled, onRecorded }: {
  disabled?: boolean;
  onRecorded: (file: File) => Promise<void>;
}) {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState("");
  const recordingRef = useRef(false);
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const sampleRateRef = useRef(44100);
  const tickerRef = useRef<number | null>(null);
  const limitRef = useRef<number | null>(null);

  const release = () => {
    if (tickerRef.current) window.clearInterval(tickerRef.current);
    if (limitRef.current) window.clearTimeout(limitRef.current);
    processorRef.current?.disconnect(); sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach(track => track.stop());
    contextRef.current?.close().catch(() => {});
    tickerRef.current = null; limitRef.current = null;
    processorRef.current = null; sourceRef.current = null;
    streamRef.current = null; contextRef.current = null;
  };

  const stop = async () => {
    if (!recordingRef.current) return;
    recordingRef.current = false;
    const blob = encodeWav(chunksRef.current, sampleRateRef.current);
    release(); setRecording(false);
    if (blob.size < 1000) { setError("Recording was too short. Please try again."); return; }
    await onRecorded(new File([blob], `field-note-${Date.now()}.wav`, { type: "audio/wav" }));
  };

  const start = async () => {
    setError("");
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Microphone capture is unavailable here. Upload a WAV or MP3 instead.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      chunksRef.current = [];
      processor.onaudioprocess = event => {
        chunksRef.current.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      source.connect(processor); processor.connect(context.destination);
      streamRef.current = stream; contextRef.current = context;
      sourceRef.current = source; processorRef.current = processor;
      sampleRateRef.current = context.sampleRate;
      setSeconds(0); recordingRef.current = true; setRecording(true);
      tickerRef.current = window.setInterval(() => setSeconds(s => s + 1), 1000);
      limitRef.current = window.setTimeout(() => { void stop(); }, 30_000);
    } catch {
      recordingRef.current = false; release(); setRecording(false);
      setError("Microphone permission was not granted. You can still upload a voice file.");
    }
  };

  useEffect(() => () => release(), []);

  return (
    <div>
      <button className={recording ? "capture-action recording" : "capture-action"}
              disabled={disabled} onClick={() => recording ? void stop() : void start()}>
        <span aria-hidden="true">{recording ? "■" : "●"}</span>
        {recording ? `Stop recording · ${seconds}s` : "Record voice note"}
      </button>
      <div className="notice" style={{ marginTop: 8 }}>
        Maximum 30 seconds. You review the transcript before it is treated as a consultant statement.
      </div>
      {error && <div className="error-box" role="alert">{error}</div>}
    </div>
  );
}
