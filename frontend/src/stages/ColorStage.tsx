/** ③ 色彩管理：ICC 档案库（扫描/搜索/筛选/质量校验）+ 软打样配置 + 取色 Lab。 */

import { useEffect, useState } from "react";
import { useEditor } from "../store/editor";
import { api, type ProfileInfo, type ProfileValidationReport } from "../lib/api";
import { Icon } from "../components/icons";
import { useStageViews } from "../flow/stages";

const CLASS_LABEL: Record<string, string> = {
  mntr: "显示器",
  prtr: "印刷机",
  scnr: "扫描仪",
  spac: "色彩空间",
  link: "设备链接",
  abst: "抽象",
  nmcl: "命名色",
};

export function ColorStage() {
  const views = useStageViews();
  const profiles = useEditor((s) => s.profiles);
  const loadProfiles = useEditor((s) => s.loadProfiles);
  const deviceProfilePath = useEditor((s) => s.deviceProfilePath);
  const proofIntent = useEditor((s) => s.proofIntent);
  const proofBpc = useEditor((s) => s.proofBpc);
  const proofThreshold = useEditor((s) => s.proofThreshold);
  const setProof = useEditor((s) => s.setProof);
  const picked = useEditor((s) => s.picked);
  const busy = useEditor((s) => s.busy);
  const busyTask = useEditor((s) => s.busyTask);

  const [filter, setFilter] = useState("");
  const [classFilter, setClassFilter] = useState("");

  useEffect(() => {
    void loadProfiles();
  }, [loadProfiles]);

  const shown = profiles.filter((p) => {
    if (classFilter && p.device_class !== classFilter) {
      return false;
    }
    return filter ? (p.description + " " + p.path).toLowerCase().includes(filter.toLowerCase()) : true;
  });

  return (
    <>
      <div className="row">
        <input
          type="text"
          placeholder="搜索 profile…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ flex: 1 }}
        />
        <select value={classFilter} onChange={(e) => setClassFilter(e.target.value)} style={{ flex: "0 0 auto" }} aria-label="设备类别筛选">
          <option value="">全部</option>
          <option value="mntr">显示器</option>
          <option value="prtr">印刷机</option>
          <option value="scnr">扫描仪</option>
          <option value="spac">色彩空间</option>
        </select>
      </div>

      <div className="profile-list">
        {shown.map((p) => (
          <ProfileRow
            key={p.path}
            profile={p}
            selected={p.path === deviceProfilePath}
            disabled={busy}
          />
        ))}
        {shown.length === 0 ? <p className="hint">没有匹配的 profile（共扫描 {profiles.length} 个）。</p> : null}
      </div>
      <p className="hint">
        共 {profiles.length} 个系统 profile。点击「用作输出」指定软打样/取色基准；
        「体检」运行 19 条结构校验规则。
      </p>

      <div className="divider" />
      <div className="row">
        <span className="section-title" style={{ margin: 0, flex: 1 }}>
          软打样配置
        </span>
        {views.color.status === "done" ? <span className="badge accent">已指定</span> : <span className="badge warn">未指定</span>}
      </div>
      <p className="hint" style={{ margin: "0 0 6px" }}>
        输出档案：{chosenDescription(profiles, deviceProfilePath)}
      </p>
      <div className="row">
        <label>意图</label>
        <select value={proofIntent} onChange={(e) => setProof({ proofIntent: e.target.value })} aria-label="渲染意图">
          <option value="perceptual">感知（照片）</option>
          <option value="relative">相对比色（默认）</option>
          <option value="saturation">饱和度</option>
          <option value="absolute">绝对比色（打样）</option>
        </select>
      </div>
      <div className="row">
        <label>黑点补偿</label>
        <input
          type="checkbox"
          checked={proofBpc}
          onChange={(e) => setProof({ proofBpc: e.target.checked })}
        />
        <label style={{ marginLeft: 10 }}>越界阈值 ΔE</label>
        <input
          type="number"
          min={0.5}
          max={20}
          step={0.5}
          style={{ width: 64, flex: "0 0 auto" }}
          value={proofThreshold}
          onChange={(e) => setProof({ proofThreshold: Number(e.target.value) || 4 })}
        />
      </div>

      <div className="divider" />
      <p className="hint">取色（工具列 💧 或按 I）</p>
      {picked ? (
        <div className="row">
          <span
            className="swatch"
            style={{ background: `rgb(${picked.rgb[0]},${picked.rgb[1]},${picked.rgb[2]})` }}
          />
          <span className="hint" style={{ margin: 0 }}>
            RGB {picked.rgb.join(", ")}
            {picked.lab
              ? ` → L*${picked.lab[0].toFixed(1)} a*${picked.lab[1].toFixed(1)} b*${picked.lab[2].toFixed(1)}`
              : " → 换算中…"}
          </span>
        </div>
      ) : (
        <p className="hint">用取色工具点击画布任意位置，读取该像素的 RGB 与 Lab。</p>
      )}
      {busy && busyTask === "validate" ? <p className="hint">档案体检中…</p> : null}
    </>
  );
}

function chosenDescription(profiles: ProfileInfo[], path: string): string {
  if (!path) {
    return "未指定（打样将以内置 sRGB 演示）";
  }
  const p = profiles.find((x) => x.path === path);
  return p ? `${p.description || p.path}（${CLASS_LABEL[p.device_class] ?? p.device_class} · ${p.color_space.trim()}）` : path;
}

function ProfileRow({
  profile,
  selected,
  disabled,
}: {
  profile: ProfileInfo;
  selected: boolean;
  disabled: boolean;
}) {
  const setProof = useEditor((s) => s.setProof);
  const setBusy = useEditor((s) => s.setBusy);
  const toast = useEditor((s) => s.toast);
  const [report, setReport] = useState<ProfileValidationReport | null>(null);
  const [open, setOpen] = useState(false);

  const validate = async () => {
    setBusy(true, "validate");
    try {
      const r = await api.validateProfile(profile.path);
      setReport(r.report);
      setOpen(true);
      toast(
        r.report.valid ? `档案体检通过：${profile.description || profile.path}` : `档案体检发现 ${r.report.counts.error} 个错误`,
        r.report.valid ? "ok" : "error",
      );
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className={"profile-item" + (selected ? " selected" : "")}>
        <span className="pname" title={profile.path}>
          {profile.description || profile.path}
        </span>
        <span className="pspace">{profile.color_space.trim() || "?"}</span>
        {report ? (
          <span className={"badge " + (report.valid ? "ok" : "bad")}>{report.valid ? "通过" : `${report.counts.error}错`}</span>
        ) : null}
        <button className="small ghost" disabled={disabled} title="结构体检（19 条规则）" onClick={() => void validate()}>
          体检
        </button>
        <button
          className="small ghost"
          disabled={disabled || selected}
          title="指定为软打样/取色输出档案"
          onClick={() => setProof({ deviceProfilePath: profile.path })}
        >
          {selected ? "使用中" : "用作输出"}
        </button>
        {report && report.issues.length > 0 ? (
          <button className="small ghost" title="展开/收起体检明细" onClick={() => setOpen((v) => !v)}>
            <Icon name="chevron-down" size={12} style={{ transform: open ? "rotate(0deg)" : "rotate(-90deg)" }} />
          </button>
        ) : null}
      </div>
      {report && open ? (
        <div className="profile-issue">
          {report.issues.slice(0, 8).map((issue, i) => (
            <div key={i}>
              <span className={`sev-${issue.severity.toLowerCase()}`}>{issue.severity}</span>{" "}
              {issue.message}
              {issue.location ? `（${issue.location}）` : ""}
            </div>
          ))}
          {report.issues.length > 8 ? <div>…共 {report.issues.length} 条</div> : null}
        </div>
      ) : null}
    </>
  );
}
