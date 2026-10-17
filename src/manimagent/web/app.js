const element = (id) => document.getElementById(id);
const terminal = new Set(["completed", "needs_input", "needs_review", "failed", "interrupted"]);
const stages = {
  queued: "排队中", planning: "规划讲解", coding: "生成或修订代码",
  rendering: "渲染预览", reviewing: "检查画面", finalizing: "正式渲染与检查", assembling: "合成视频",
};
const states = {
  completed: "已完成", needs_input: "请补充输入", needs_review: "结果待检查",
  failed: "生成失败", interrupted: "任务已中断",
};
let timer;
let currentJob = null;

function countCharacters() {
  const count = Array.from(element("text").value.trim()).length;
  element("count").textContent = `${count} / 3000`;
  return count;
}

async function requestJSON(url, options) {
  const response = await fetch(url, options);
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("服务返回异常，请检查连接");
  }
  if (!response.ok) {
    let message = typeof data.detail === "string" ? data.detail : "请输入 1 至 3000 个字符";
    if (response.status === 503) message = "生成服务尚未就绪，请完成模型与渲染环境配置后重启服务。";
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return data;
}

function releaseForm() {
  clearTimeout(timer);
  element("generate").disabled = false;
  localStorage.removeItem("manimagent.job");
}

function showResults(data) {
  element("result").replaceChildren();
  element("result").classList.remove("empty");
  element("artifacts").replaceChildren();
  const videos = data.artifacts.filter((a) => ["final", "scene_video", "preview"].includes(a.kind));
  const preferred = videos.find((a) => a.artifact_id === data.final_artifact_id)
    || videos.find((a) => a.kind === "scene_video") || videos.at(-1);
  if (preferred) {
    const video = document.createElement("video");
    video.controls = true;
    video.preload = "metadata";
    video.src = preferred.url;
    element("result").append(video);
  }
  for (const artifact of data.artifacts) {
    if (!["final", "scene_video", "preview", "report", "plan"].includes(artifact.kind)) continue;
    const link = document.createElement("a");
    link.className = "artifact";
    link.href = `${artifact.url}?download=true`;
    link.textContent = `${artifact.kind === "final" ? "下载视频" : "下载"} · ${artifact.label}`;
    element("artifacts").append(link);
  }
}

async function pollJob() {
  try {
    const data = await requestJSON(`/api/jobs/${currentJob}`);
    element("status").style.display = "block";
    element("status").textContent = states[data.status] || stages[data.stage] || "生成中";
    element("message").textContent = data.message;
    element("job").textContent = `任务 ${data.job_id} · 场景 ${data.completed_scenes} / ${data.total_scenes}`;
    if (terminal.has(data.status)) {
      releaseForm();
      showResults(data);
    } else {
      timer = setTimeout(pollJob, 2000);
    }
  } catch (error) {
    if (error.status === 404) {
      releaseForm();
      element("message").textContent = "历史任务不存在，请重新提交。";
    } else {
      element("message").textContent = `${error.message}，正在重连…`;
      timer = setTimeout(pollJob, 4000);
    }
  }
}

element("text").addEventListener("input", countCharacters);
element("example").addEventListener("click", () => {
  element("text").value = "请用动画解释二维向量投影。用 a=(3,2)、b=(1,0) 举例，先展示两个向量，再展示投影和垂线，最后区分投影系数与投影向量。面向初学者讲解。";
  countCharacters();
  element("text").focus();
});

element("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  element("error").textContent = "";
  const count = countCharacters();
  if (count < 1 || count > 3000) {
    element("error").textContent = "请输入 1 至 3000 个字符";
    return;
  }
  element("generate").disabled = true;
  clearTimeout(timer);
  element("message").textContent = "正在提交任务…";
  try {
    const data = await requestJSON("/api/jobs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: element("text").value }),
    });
    element("result").replaceChildren();
    element("artifacts").replaceChildren();
    currentJob = data.job_id;
    localStorage.setItem("manimagent.job", currentJob);
    pollJob();
  } catch (error) {
    element("message").textContent = "";
    element("error").textContent = error.message;
    element("generate").disabled = false;
  }
});

requestJSON("/api/health").then((data) => {
  if (!data.ready) {
    element("health").textContent = "生成服务尚未就绪，请先完成模型与渲染环境配置。";
    element("health").style.display = "block";
  }
}).catch(() => {});

const savedJob = localStorage.getItem("manimagent.job");
if (savedJob && /^[a-f0-9]{32}$/.test(savedJob)) {
  currentJob = savedJob;
  element("generate").disabled = true;
  pollJob();
}
