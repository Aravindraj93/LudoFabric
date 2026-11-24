// Auto-installed by GameAgent. Correct import settings for downloaded third-party assets.
using UnityEditor;

namespace GameAgent.Bridge
{
    public class ThirdPartyImportPostprocessor : AssetPostprocessor
    {
        void OnPreprocessTexture()
        {
            if (!assetPath.Contains("/ThirdParty/")) return;
            var ti = (TextureImporter)assetImporter;
            var lower = assetPath.ToLowerInvariant();

            if (lower.Contains("/polyhaven/hdri/"))
            {
                ti.textureShape = TextureImporterShape.TextureCube;
                ti.generateCubemap = TextureImporterGenerateCubemap.AutoCubemap;
                ti.mipmapEnabled = true;
                return;
            }
            if (lower.Contains("_nor_gl") || lower.Contains("_normal"))
            {
                ti.textureType = TextureImporterType.NormalMap;
                return;
            }
            if (lower.Contains("_rough") || lower.Contains("_ao") || lower.Contains("_arm") || lower.Contains("_disp"))
                ti.sRGBTexture = false;
        }
    }
}

