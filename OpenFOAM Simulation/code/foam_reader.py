"""ParaView reader setup shared by CSV and video export."""
def open_volume(marker, arrays):
    from paraview.simple import OpenFOAMReader
    # Set before the first information request, then rescan cached directory times.
    reader = OpenFOAMReader(FileName=str(marker), SkipZeroTime=0)
    if 'ListtimestepsaccordingtocontrolDict' in reader.ListProperties():
        reader.ListtimestepsaccordingtocontrolDict = 0
    elif 'ListTimeStepsByControlDict' in reader.ListProperties():
        reader.ListTimeStepsByControlDict = 0
    reader.MeshRegions = ['internalMesh']
    reader.CellArrays = arrays
    reader.UpdateVTKObjects()
    if 'Refresh' in reader.ListProperties():
        # Refresh is a command property (button), not a numeric property.
        reader.InvokeCommand('Refresh')
    reader.UpdatePipelineInformation()
    times = [float(t) for t in reader.TimestepValues]
    if not any(abs(t) < 1e-9 for t in times):
        raise ValueError('Reader rescan still excludes 0; available times: '
                         + repr(times[:5]) + ' ... ' + repr(times[-5:]))
    print(f'Reader: {len(times)} saved times, {times[0]:g}..{times[-1]:g} s', flush=True)
    return reader
