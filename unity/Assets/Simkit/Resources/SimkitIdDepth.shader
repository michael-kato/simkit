// Writes each renderer's Renderer Shader User Value (its simkit instance id)
// to target 0 and its eye depth in meters (along the camera's forward axis) to target 1.
Shader "Hidden/Simkit/IdDepth"
{
    SubShader
    {
        Tags { "RenderPipeline" = "UniversalPipeline" "RenderType" = "Opaque" }
        Pass
        {
            Name "IdDepth"
            ZWrite On
            ZTest LEqual
            Cull Back

            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #pragma multi_compile_instancing
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct Attributes
            {
                float4 positionOS : POSITION;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float eyeDepth : TEXCOORD0;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Targets
            {
                uint id : SV_Target0;
                float depth : SV_Target1;
            };

            Varyings Vert(Attributes input)
            {
                Varyings output;
                UNITY_SETUP_INSTANCE_ID(input);
                UNITY_TRANSFER_INSTANCE_ID(input, output);
                float3 positionWS = TransformObjectToWorld(input.positionOS.xyz);
                output.positionCS = TransformWorldToHClip(positionWS);
                output.eyeDepth = -TransformWorldToView(positionWS).z;
                return output;
            }

            Targets Frag(Varyings input)
            {
                UNITY_SETUP_INSTANCE_ID(input);
                Targets targets;
                targets.id = unity_RendererUserValue;
                targets.depth = input.eyeDepth;
                return targets;
            }
            ENDHLSL
        }
    }
}
