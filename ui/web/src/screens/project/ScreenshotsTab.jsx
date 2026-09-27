import { useEffect, useState } from 'react';
import { deleteScreenshot, getProject, listScreenshots, reorderScreenshots, uploadScreenshots } from '../../api.js';
import { useAsync } from '../../hooks.js';
import ScreenshotDrop from '../../components/ScreenshotDrop.jsx';
import { useToast } from '../../components/Toast.jsx';

const rejectedText = (rejected) => (rejected || []).map((r) => `“${r.file}”: ${r.reason}`).join(' ');

// Project → Screenshots. Props: { name }
export default function ScreenshotsTab({ name }) {
  const toast = useToast();
  const project = useAsync(() => getProject(name), [name]);
  const shots = useAsync(() => listScreenshots(name), [name]);
  const [files, setFiles] = useState([]);
  const [message, setMessage] = useState(null);

  useEffect(() => { if (shots.data) setFiles(shots.data); }, [shots.data]);

  const act = async (fn) => {
    try { return await fn(); } catch (e) { toast(e.message, 'error'); shots.reload(); return null; }
  };

  const onAdd = (list) => act(async () => {
    const res = await uploadScreenshots(name, list);
    setFiles(res.screenshots || []);
    setMessage(rejectedText(res.rejected) || null);
  });
  const onRemove = (file) => act(async () => { setFiles(await deleteScreenshot(name, file)); setMessage(null); });
  const onReorder = (order) => {
    setFiles(order.map((f) => files.find((x) => x.file === f))); // show the new order right away
    act(async () => setFiles(await reorderScreenshots(name, order)));
  };

  if (shots.error && !shots.data) return <div className="rs-danger-text">{shots.error.message}</div>;
  const hasRun = project.data && project.data.status && project.data.status !== 'draft';
  return <ScreenshotDrop files={files} onAdd={onAdd} onRemove={onRemove} onReorder={onReorder} readOnlyWarning={!!hasRun} message={message} />;
}
