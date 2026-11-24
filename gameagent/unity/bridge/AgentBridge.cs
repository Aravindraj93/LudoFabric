// Auto-installed by GameAgent. Headless entry points invoked via -executeMethod.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;

namespace GameAgent.Bridge
{
    public static class AgentBridge
    {
        static readonly List<string> Errors = new List<string>();
        static readonly List<string> Info = new List<string>();

        static string Arg(string name)
        {
            var a = Environment.GetCommandLineArgs();
            for (int i = 0; i < a.Length - 1; i++)
                if (a[i] == name) return a[i + 1];
            return null;
        }

        static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"")
            .Replace("\r", "").Replace("\n", "\\n").Replace("\t", "\\t");

        static void WriteResult(bool ok)
        {
            var path = Arg("-agentResult");
            if (string.IsNullOrEmpty(path)) return;
            var sb = new StringBuilder();
            sb.Append("{\"ok\":").Append(ok ? "true" : "false");
            sb.Append(",\"errors\":[").Append(string.Join(",", Errors.Select(e => "\"" + Esc(e) + "\""))).Append("]");
            sb.Append(",\"info\":[").Append(string.Join(",", Info.Select(e => "\"" + Esc(e) + "\""))).Append("]}");
            File.WriteAllText(path, sb.ToString());
        }

        static void OnLog(string condition, string stackTrace, LogType type)
        {
            if (type == LogType.Exception || type == LogType.Error)
                Errors.Add(condition + (type == LogType.Exception ? "\n" + stackTrace : ""));
        }

        // ------------------------------------------------------------------
        public static void Ping()
        {
            Info.Add("bridge compiled");
            WriteResult(true);
        }

        public static void Setup()
        {
            try
            {
                EnsureUrp();
                PlayerSettings.colorSpace = ColorSpace.Linear;
                AssetDatabase.SaveAssets();
                WriteResult(true);
            }
            catch (Exception e)
            {
                Errors.Add(e.ToString());
                WriteResult(false);
            }
        }

        static void EnsureUrp()
        {
            if (GraphicsSettings.defaultRenderPipeline is UniversalRenderPipelineAsset)
            {
                Info.Add("URP already active");
                return;
            }
            SceneKit.EnsureFolder("Assets/Settings");
            var rendererData = ScriptableObject.CreateInstance<UniversalRendererData>();
            AssetDatabase.CreateAsset(rendererData, "Assets/Settings/GA_URP_Renderer.asset");
            var asset = UniversalRenderPipelineAsset.Create(rendererData);
            asset.supportsHDR = true;
            asset.msaaSampleCount = 4;
            asset.shadowDistance = 80f;
            AssetDatabase.CreateAsset(asset, "Assets/Settings/GA_URP_Asset.asset");
            GraphicsSettings.defaultRenderPipeline = asset;
            int current = QualitySettings.GetQualityLevel();
            for (int i = 0; i < QualitySettings.names.Length; i++)
            {
                QualitySettings.SetQualityLevel(i, false);
                QualitySettings.renderPipeline = asset;
            }
            QualitySettings.SetQualityLevel(current, false);
            Info.Add("URP asset created and assigned");
        }

        // ------------------------------------------------------------------
        public static void RunMethods()
        {
            Application.logMessageReceived += OnLog;
            bool ok = true;
            var list = (Arg("-agentMethods") ?? "").Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries);
            var assemblies = AppDomain.CurrentDomain.GetAssemblies();
            foreach (var raw in list)
            {
                var full = raw.Trim();
                int idx = full.LastIndexOf('.');
                if (idx <= 0) { Errors.Add($"{full}: invalid method name"); ok = false; continue; }
                var typeName = full.Substring(0, idx);
                var methodName = full.Substring(idx + 1);
                var type = assemblies.Select(a => a.GetType(typeName)).FirstOrDefault(t => t != null);
                if (type == null) { Errors.Add($"{full}: type '{typeName}' not found"); ok = false; continue; }
                var m = type.GetMethod(methodName, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static,
                    null, Type.EmptyTypes, null);
                if (m == null) { Errors.Add($"{full}: parameterless static method not found"); ok = false; continue; }
                int before = Errors.Count;
                try
                {
                    m.Invoke(null, null);
                }
                catch (TargetInvocationException e)
                {
                    Errors.Add($"{full} threw: {e.InnerException}");
                    ok = false;
                }
                if (Errors.Count > before) ok = false;
                else Info.Add($"{full}: ok");
                SaveDirtyScenes();
                AssetDatabase.SaveAssets();
            }
            AssetDatabase.Refresh();
            SyncBuildScenes();
            Application.logMessageReceived -= OnLog;
            WriteResult(ok);
        }

        static void SaveDirtyScenes()
        {
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var s = SceneManager.GetSceneAt(i);
                if (s.isDirty && !string.IsNullOrEmpty(s.path)) EditorSceneManager.SaveScene(s);
            }
        }

        static void SyncBuildScenes()
        {
            var scenes = EditorBuildSettings.scenes.Where(s => File.Exists(s.path)).ToList();
            if (scenes.Count == 0)
            {
                scenes = AssetDatabase.FindAssets("t:Scene", new[] { "Assets" })
                    .Select(AssetDatabase.GUIDToAssetPath)
                    .Where(p => !p.Contains("/ThirdParty/") && !p.Contains("/Samples/"))
                    .Select(p => new EditorBuildSettingsScene(p, true)).ToList();
            }
            EditorBuildSettings.scenes = scenes.ToArray();
            Info.Add("build scenes: " + string.Join(", ", scenes.Select(s => s.path)));
        }

        // ------------------------------------------------------------------
        public static void CaptureScreenshots()
        {
            Application.logMessageReceived += OnLog;
            var outDir = Arg("-agentOut") ?? "Screenshots";
            Directory.CreateDirectory(outDir);
            var scenes = EditorBuildSettings.scenes.Where(s => s.enabled && File.Exists(s.path)).Select(s => s.path).ToList();
            int index = 0;
            foreach (var path in scenes)
            {
                try
                {
                    EditorSceneManager.OpenScene(path, OpenSceneMode.Single);
                    var name = $"{index:00}_{Path.GetFileNameWithoutExtension(path)}";
                    var cams = UnityEngine.Object.FindObjectsByType<Camera>(FindObjectsSortMode.None)
                        .Where(c => c.enabled && c.targetTexture == null)
                        .OrderByDescending(c => c.CompareTag("MainCamera")).ThenByDescending(c => c.depth).ToList();
                    if (cams.Count == 0) { Errors.Add($"{path}: scene has no camera"); index++; continue; }
                    Capture(cams[0], Path.Combine(outDir, name + "_gameplay.png"));

                    var bounds = SceneBounds();
                    if (bounds.HasValue)
                    {
                        var go = new GameObject("GA_OverviewCam");
                        var oc = go.AddComponent<Camera>();
                        oc.CopyFrom(cams[0]);
                        var b = bounds.Value;
                        float dist = Mathf.Max(b.extents.magnitude, 5f) * 1.2f;
                        go.transform.position = b.center + new Vector3(-0.7f, 0.6f, -0.7f).normalized * dist;
                        go.transform.LookAt(b.center);
                        oc.farClipPlane = Mathf.Max(oc.farClipPlane, dist * 4f);
                        Capture(oc, Path.Combine(outDir, name + "_overview.png"));
                        UnityEngine.Object.DestroyImmediate(go);
                    }
                }
                catch (Exception e)
                {
                    Errors.Add($"{path}: {e}");
                }
                index++;
            }
            Application.logMessageReceived -= OnLog;
            WriteResult(true);
        }

        static Bounds? SceneBounds()
        {
            var renderers = UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)
                .Where(r => r.enabled && !(r is ParticleSystemRenderer)).ToArray();
            if (renderers.Length == 0) return null;
            var b = renderers[0].bounds;
            foreach (var r in renderers) b.Encapsulate(r.bounds);
            return b;
        }

        static void Capture(Camera cam, string file)
        {
            const int w = 1280, h = 720;
            var rt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB) { antiAliasing = 4 };
            var prevTarget = cam.targetTexture;
            var prevActive = RenderTexture.active;
            cam.targetTexture = rt;
            cam.Render();
            RenderTexture.active = rt;
            var tex = new Texture2D(w, h, TextureFormat.RGB24, false);
            tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
            tex.Apply();
            File.WriteAllBytes(file, tex.EncodeToPNG());
            cam.targetTexture = prevTarget;
            RenderTexture.active = prevActive;
            UnityEngine.Object.DestroyImmediate(tex);
            rt.Release();
            UnityEngine.Object.DestroyImmediate(rt);
            Info.Add("screenshot " + file);
        }

        // ------------------------------------------------------------------
        public static void Build()
        {
            var outPath = Arg("-agentBuildPath") ?? "Build/Game.exe";
            var scenes = EditorBuildSettings.scenes.Where(s => s.enabled && File.Exists(s.path)).Select(s => s.path).ToArray();
            if (scenes.Length == 0) { Errors.Add("No scenes in build settings"); WriteResult(false); return; }
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = scenes,
                locationPathName = outPath,
                target = BuildTarget.StandaloneWindows64,
                options = BuildOptions.None,
            });
            bool ok = report.summary.result == BuildResult.Succeeded;
            if (!ok)
                foreach (var step in report.steps)
                foreach (var msg in step.messages)
                    if (msg.type == LogType.Error || msg.type == LogType.Exception)
                        Errors.Add(msg.content);
            Info.Add($"build {report.summary.result}, size {report.summary.totalSize} bytes");
            WriteResult(ok);
        }
    }
}

