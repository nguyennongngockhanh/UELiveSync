// Copyright Epic Games, Inc. All Rights Reserved.

using UnrealBuildTool;
using System.IO;

public class UELiveSync : ModuleRules
{
	public UELiveSync(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicIncludePaths.AddRange(
			new string[] {

			}
		);

		PrivateIncludePaths.AddRange(
			new string[] {
				// Phase 1.3: Shared protocol serializer/deserializer
				// ModuleDirectory = .../UE_Plugin/UELiveSync/Source/UELiveSync
				// Target          = .../Shared/Serializer
				Path.Combine(ModuleDirectory, "..", "..", "..", "..", "Shared", "Serializer"),
			}
		);

		PublicDependencyModuleNames.AddRange(
			new string[]
			{
				"Core",
				"CoreUObject",
				"Engine",
				"InputCore",

				// Phase 7E: Sequencer + Keyframe Replication
				"LevelSequence",
				"MovieScene",
				"MovieSceneTracks",
			}
		);

		PrivateDependencyModuleNames.AddRange(
			new string[]
			{
				"CoreUObject",
				"Engine",

				"Slate",
				"SlateCore",

				"Sockets",
				"Networking",

				"Json",
				"JsonUtilities",
				"ProceduralMeshComponent",

				"UnrealEd",
				"AssetTools",
				"AssetRegistry"
			}
		);

		DynamicallyLoadedModuleNames.AddRange(
			new string[]
			{

			}
		);
	}
}