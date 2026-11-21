import React, { useEffect, useRef, useState } from "react";
import type { Artefact } from "./model";

const MAX_FILE = 32 * 1024 * 1024;
function previewType(data: Uint8Array) {
  if (data.length >= 5 && String.fromCharCode(...data.slice(0, 5)) === "%PDF-")
    return "application/pdf";
  if (
    data.length >= 8 &&
    [137, 80, 78, 71, 13, 10, 26, 10].every((n, i) => data[i] === n)
  )
    return "image/png";
  if (data.length >= 3 && data[0] === 255 && data[1] === 216 && data[2] === 255)
    return "image/jpeg";
  return null;
}
/** Read-only delivery: never place credentials in links or render active HTML/SVG. */
export function ArtefactDelivery({
  file,
  mode,
  token,
}: {
  file?: Artefact;
  mode: string;
  token: string;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [preview, setPreview] = useState<{ url: string; type: string } | null>(
    null,
  );
  const request = useRef<AbortController | null>(null),
    serial = useRef(0);
  const identity = `${file?.id || ""}:${file?.sha256 || ""}`;
  useEffect(() => {
    serial.current++;
    request.current?.abort();
    setError("");
    setBusy(false);
    setPreview(null);
    return () => {
      serial.current++;
      request.current?.abort();
    };
  }, [identity, mode]);
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview.url);
    },
    [preview],
  );
  const available =
    mode === "local" &&
    Boolean(file?.sha256) &&
    file?.exists !== false &&
    file?.downloadable !== false &&
    file?.download_available !== false &&
    file?.download_allowed !== false;
  async function receive(inline: boolean) {
    if (!available || !file || !token) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const ticket = ++serial.current;
    setBusy(true);
    setError("");
    setPreview(null);
    try {
      const query = new URLSearchParams({ id: file.id, sha256: file.sha256! });
      const response = await fetch(`/api/artefact?${query}`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
      });
      if (!response.ok) {
        let message = "산출물을 받을 수 없습니다.";
        try {
          const body = await response.json();
          if (typeof body.error === "string") message = body.error;
        } catch {}
        throw new Error(message);
      }
      const size = Number(response.headers.get("content-length"));
      if (Number.isFinite(size) && size > MAX_FILE)
        throw new Error(
          "32MiB 이하의 산출물만 관제에서 받을 수 있습니다. 원본은 코딩 도구에서 확인하세요.",
        );
      if (!response.body) throw new Error("파일 응답이 비어 있습니다.");
      const reader = response.body.getReader(),
        chunks: Uint8Array[] = [];
      let total = 0;
      try {
        while (true) {
          const next = await reader.read();
          if (next.done) break;
          total += next.value.byteLength;
          if (total > MAX_FILE) {
            await reader.cancel();
            throw new Error("관제 파일 크기 제한을 초과했습니다.");
          }
          chunks.push(next.value);
        }
      } finally {
        reader.releaseLock();
      }
      const data = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) {
        data.set(chunk, offset);
        offset += chunk.byteLength;
      }
      if (controller.signal.aborted || ticket !== serial.current) return;
      const type = previewType(new Uint8Array(data));
      if (inline && !type)
        throw new Error(
          "이 파일 형식은 미리보기 대신 다운로드로 확인하세요. HTML·SVG는 관제에서 실행하지 않습니다.",
        );
      const url = URL.createObjectURL(
        new Blob([data], { type: inline ? type! : "application/octet-stream" }),
      );
      if (inline) setPreview({ url, type: type! });
      else {
        const link = document.createElement("a");
        link.href = url;
        link.download =
          file.path
            .split("/")
            .at(-1)
            ?.replace(/[\u0000-\u001f]/g, "") || "artefact";
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (e: any) {
      if (!controller.signal.aborted && ticket === serial.current)
        setError(e.message || "산출물 수신 오류");
    } finally {
      if (ticket === serial.current) setBusy(false);
    }
  }
  return (
    <section className="artefact-delivery" aria-label="현재 산출물 받기">
      <div className="delivery-heading">
        <strong>현재 파일 받기</strong>
        <span>{file?.sha256 ? "등록된 SHA256 기준" : "파일 버전 미확인"}</span>
      </div>
      <div className="delivery-actions">
        <button
          className="quiet-button"
          disabled={!available || busy || !token}
          onClick={() => receive(false)}
        >
          다운로드
        </button>
        <button
          className="quiet-button"
          disabled={!available || busy || !token}
          onClick={() => receive(true)}
        >
          미리보기
        </button>
        {busy && <span role="status">현재 파일 버전을 확인하고 있습니다.</span>}
      </div>
      {!available && (
        <p className="fine-print">
          {mode !== "local"
            ? "데모·저장 스냅샷은 실제 파일 서버와 연결되지 않습니다."
            : "등록된 현재 파일과 해시가 있어야 받을 수 있습니다."}
        </p>
      )}
      <p className="fine-print">
        파일 수령은 사업 승인·외부 제출이 아닙니다. 실행 검증과 시각·내용 검수
        범위를 별도로 확인하세요.
      </p>
      {error && (
        <p className="delivery-error" role="alert">
          {error}
        </p>
      )}
      {preview && (
        <div className="artefact-preview">
          <button className="quiet-button" onClick={() => setPreview(null)}>
            미리보기 닫기
          </button>
          {preview.type === "application/pdf" ? (
            <iframe
              title={`PDF 미리보기 ${file?.path}`}
              src={preview.url}
              sandbox=""
            />
          ) : (
            <img
              src={preview.url}
              alt={`현재 산출물 미리보기 ${file?.path}`}
              onError={() => {
                setPreview(null);
                setError(
                  "이미지를 읽을 수 없습니다. 원본 파일을 다운로드해 확인하세요.",
                );
              }}
            />
          )}
        </div>
      )}
    </section>
  );
}
