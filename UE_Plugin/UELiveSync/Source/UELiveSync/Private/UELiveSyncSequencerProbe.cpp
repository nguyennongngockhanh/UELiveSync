// ==========================================================
// Phase 7E — Sequencer Compile Probe
// ==========================================================
// Compile-gated validation that the minimal Sequencer API
// compiles against UE5.7.4 with the added module dependencies.
//
// Safety:
//   - The probe function is never called at runtime.
//   - No sequence assets are created.
//   - No actor binding occurs.
//   - No editor viewport or Sequencer UI interaction.
//
// Guard: defined(SEQUENCER_PROBE) must be set at build time
// to compile the probe body. Without this define only the
// includes are validated.
// ==========================================================

#include "CoreMinimal.h"

// Phase 7E: Sequencer module includes — validate header resolution
#include "LevelSequence.h"
#include "MovieScene.h"
#include "MovieSceneTrack.h"
#include "MovieSceneSection.h"
#include "MovieScenePossessable.h"
#include "MovieSceneBinding.h"
#include "MovieSceneObjectBindingID.h"
#include "Sections/MovieSceneBoolSection.h"
#include "Channels/MovieSceneBoolChannel.h"
#include "Channels/MovieSceneDoubleChannel.h"
#include "Tracks/MovieScene3DTransformTrack.h"
#include "Sections/MovieScene3DTransformSection.h"
#include "Tracks/MovieSceneBoolTrack.h"
#include "Tracks/MovieSceneCameraCutTrack.h"
#include "Sections/MovieSceneCameraCutSection.h"
#include "Tracks/MovieScenePropertyTrack.h"


#if defined(SEQUENCER_PROBE) && SEQUENCER_PROBE

namespace SequencerCompileProbe
{

void ValidateSequencerAPI()
{
    // --------------------------------------------------
    // Type validation via pointer declarations
    // --------------------------------------------------
    ULevelSequence* LevelSequence = nullptr;
    UMovieScene*    MovieScene    = nullptr;

    UMovieScene3DTransformTrack*  TransformTrack  = nullptr;
    UMovieScene3DTransformSection* TransformSection = nullptr;
    UMovieSceneBoolTrack*         BoolTrack       = nullptr;
    UMovieSceneBoolSection*       BoolSection     = nullptr;
    UMovieSceneCameraCutTrack*    CameraCutTrack  = nullptr;
    UMovieSceneCameraCutSection*  CameraCutSection = nullptr;

    FMovieScenePossessable*        Possessable    = nullptr;
    FMovieSceneObjectBindingID*    BindingID      = nullptr;
    FMovieSceneDoubleChannel*      DoubleChannel  = nullptr;
    FMovieSceneBoolChannel*        BoolChannel    = nullptr;

    // --------------------------------------------------
    // FMovieScenePossessable construction
    // --------------------------------------------------
    FMovieScenePossessable TestPossessable(
        TEXT("ProbeObject"),
        AActor::StaticClass());

    // --------------------------------------------------
    // FMovieSceneObjectBindingID construction
    // --------------------------------------------------
    FGuid TestGuid(0, 0, 0, 1);
    FMovieSceneObjectBindingID TestBindingID(
        UE::MovieScene::FRelativeObjectBindingID(TestGuid));

    // --------------------------------------------------
    // UMovieScene::AddPossessable — runtime-safe
    // --------------------------------------------------
    FGuid ObjectGuid = MovieScene->AddPossessable(
        TEXT("ProbeObject"),
        AActor::StaticClass());

    // --------------------------------------------------
    // ULevelSequence::BindPossessableObject — runtime-safe
    // --------------------------------------------------
    LevelSequence->BindPossessableObject(
        ObjectGuid,
        *(AActor*)nullptr,
        (UObject*)nullptr);

    // --------------------------------------------------
    // UMovieScene::AddTrack<UMovieScene3DTransformTrack>
    // --------------------------------------------------
    TransformTrack = MovieScene->AddTrack<UMovieScene3DTransformTrack>(
        ObjectGuid);

    // --------------------------------------------------
    // UMovieSceneTrack::CreateNewSection + cast
    // --------------------------------------------------
    UMovieSceneSection* NewSection = TransformTrack->CreateNewSection();
    TransformSection = Cast<UMovieScene3DTransformSection>(NewSection);

    // --------------------------------------------------
    // UMovieScene3DTransformSection::GetChannelProxy
    // --------------------------------------------------
    FMovieSceneChannelProxy& ChannelProxy =
        TransformSection->GetChannelProxy();

    // --------------------------------------------------
    // GetChannel<FMovieSceneDoubleChannel> for loc X/Y/Z
    // --------------------------------------------------
    DoubleChannel = ChannelProxy.GetChannel<FMovieSceneDoubleChannel>(0);
    FMovieSceneDoubleChannel* LocY =
        ChannelProxy.GetChannel<FMovieSceneDoubleChannel>(1);
    FMovieSceneDoubleChannel* LocZ =
        ChannelProxy.GetChannel<FMovieSceneDoubleChannel>(2);

    // --------------------------------------------------
    // FMovieSceneDoubleChannel key insertion
    // --------------------------------------------------
    DoubleChannel->AddLinearKey(FFrameNumber(0), 0.0);
    DoubleChannel->AddLinearKey(FFrameNumber(50), 250.0);
    DoubleChannel->AddCubicKey(FFrameNumber(100), 500.0);

    // --------------------------------------------------
    // Batch key insertion via UpdateOrAddKeys
    // --------------------------------------------------
    TArray<FFrameNumber> KeyTimes = { 0, 50, 100, 150 };
    TArray<FMovieSceneDoubleValue> KeyValues;
    for (int32 i = 0; i < KeyTimes.Num(); i++)
    {
        FMovieSceneDoubleValue Val;
        Val.Value     = static_cast<double>(i) * 100.0;
        Val.InterpMode = ERichCurveInterpMode::RCIM_Linear;
        KeyValues.Add(Val);
    }
    LocY->UpdateOrAddKeys(KeyTimes, KeyValues);

    // --------------------------------------------------
    // SetDefault / GetDefault
    // --------------------------------------------------
    LocZ->SetDefault(0.0);

    // --------------------------------------------------
    // UMovieScene::AddTrack<UMovieSceneBoolTrack>
    // --------------------------------------------------
    BoolTrack = MovieScene->AddTrack<UMovieSceneBoolTrack>(ObjectGuid);

    // --------------------------------------------------
    // UMovieScenePropertyTrack::SetPropertyNameAndPath
    // --------------------------------------------------
    BoolTrack->SetPropertyNameAndPath(
        FName("bHidden"),
        TEXT("bHidden"));

    // --------------------------------------------------
    // UMovieSceneBoolTrack section + channel
    // --------------------------------------------------
    UMovieSceneSection* BoolNewSection = BoolTrack->CreateNewSection();
    BoolSection = Cast<UMovieSceneBoolSection>(BoolNewSection);
    BoolChannel = &BoolSection->GetChannel();

    // --------------------------------------------------
    // FMovieSceneBoolChannel key insertion
    // --------------------------------------------------
    {
        TArray<FFrameNumber> BoolTimes = { 0, 50, 100 };
        TArray<bool>         BoolVals  = { false, true, false };
        BoolChannel->AddKeys(BoolTimes, BoolVals);
    }

    // --------------------------------------------------
    // UMovieScene::AddCameraCutTrack
    // --------------------------------------------------
    CameraCutTrack = Cast<UMovieSceneCameraCutTrack>(
        MovieScene->AddCameraCutTrack(
            UMovieSceneCameraCutTrack::StaticClass()));

    // --------------------------------------------------
    // UMovieSceneCameraCutTrack::AddNewCameraCut
    // --------------------------------------------------
    CameraCutSection = CameraCutTrack->AddNewCameraCut(
        TestBindingID,
        FFrameNumber(0));

    // --------------------------------------------------
    // UMovieSceneCameraCutSection camera binding
    // --------------------------------------------------
    CameraCutSection->SetCameraGuid(TestGuid);

    // --------------------------------------------------
    // Suppress unused-variable warnings
    // --------------------------------------------------
    (void)Possessable;
    (void)BindingID;
    (void)LevelSequence;
    (void)CameraCutSection;
}

} // namespace SequencerCompileProbe

#endif // SEQUENCER_PROBE
