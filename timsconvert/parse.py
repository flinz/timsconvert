from timsconvert.constants import *
import numpy as np
import pandas as pd
from pyTDFSDK.tims import (tims_scannum_to_oneoverk0, tims_oneoverk0_to_ccs_for_mz, tims_read_scans_v2,
                           tims_index_to_mz, tims_extract_profile_for_frame,
                           tims_extract_centroided_spectrum_for_frame_v2)
from pyTDFSDK.tsf import tsf_read_line_spectrum_v2, tsf_read_profile_spectrum_v2, tsf_index_to_mz
from pyBaf2Sql.baf import read_double


def get_encoding_dtype(encoding):
    """
    Use "encoding" command line parameter to determine numpy dtype.

    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: Numpy dtype, either float64 or float32
    :rtype: numpy.dtype
    """
    if encoding == 32:
        return np.float32
    elif encoding == 64:
        return np.float64


def get_centroid_status(mode, exclude_mobility=None):
    """
    Use "mode" command line parameter to determine whether output data is centroided in psims compatible format.

    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param exclude_mobility: Whether to include mobility data in the output files, defaults to None.
    :type exclude_mobility: bool | None
    :return: Dictionary containing standard spectrum data.
    :return: Tuple of (centroided status (bool), exclude_mobility status (bool))
    :rtype: tuple[bool]
    """
    if mode == 'profile':
        centroided = False
        exclude_mobility = True
    elif mode == 'centroid' or mode == 'raw':
        centroided = True
    return centroided, exclude_mobility


def get_baf_spectrum_polarity(acquisitionkey_dict):
    """
    Use BAF metadata to transcribe polarity into psims compatible format.

    :param acquisitionkey_dict: A row from the AcquisitionKey table in analysis.sqlite database for BAF files.
    :type acquisitionkey_dict: dict
    :return: "+" for positive mode or "-" for negative mode.
    :rtype: str
    """
    # Polarity == 0 -> 'positive'; Polarity == 1 -> 'negative"?
    if int(acquisitionkey_dict['Polarity']) == 0:
        polarity = '+'
    elif int(acquisitionkey_dict['Polarity']) == 1:
        polarity = '-'
    return polarity


def get_maldi_coords(data, maldiframeinfo_dict):
    """
    Get tuple of MALDI coordinates from analysis.tsf/analysis.tdf metadata.

    :param data: Object containing metadata from analysis.tsf/analysis.tdf database.
    :type data: timsconvert.classes.TimsconvertTsfData | timsconvert.classes.TimsconvertTdfData
    :param maldiframeinfo_dict: A row from the MaldiFrameInfo table in analysis.tsf/analysis.tdf database.
    :type maldiframeinfo_dict: dict
    :return: x-y (or x-y-z if available) coordinates for the current spectrum.
    :rtype: tuple[int]
    """
    if data.analysis['GlobalMetadata']['MaldiApplicationType'] == 'SingleSpectra':
        coords = maldiframeinfo_dict['SpotName']
    elif data.analysis['GlobalMetadata']['MaldiApplicationType'] == 'Imaging':
        coords = [int(maldiframeinfo_dict['XIndexPos']), int(maldiframeinfo_dict['YIndexPos'])]
        if 'ZIndexPos' in data.analysis['MaldiFrameInfo'].columns:
            coords.append(int(maldiframeinfo_dict['ZIndexPos']))
        coords = tuple(coords)
    return coords


def init_scan_dict():
    """
    Initialize dictionary to store spectrum data. All values are initialized as None.

    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    return {'scan_number': None,
            'scan_type': None,
            'ms_level': None,
            'mz_array': None,
            'intensity_array': None,
            'mobility_array': None,
            'polarity': None,
            'centroided': None,
            'retention_time': None,
            'coord': None,
            'total_ion_current': None,
            'base_peak_mz': None,
            'base_peak_intensity': None,
            'high_mz': None,
            'low_mz': None,
            'target_mz': None,
            'isolation_lower_offset': None,
            'isolation_upper_offset': None,
            'selected_ion_mz': None,
            'selected_ion_intensity': None,
            'selected_ion_mobility': None,
            'selected_ion_ccs': None,
            'charge_state': None,
            'collision_energy': None,
            'frame': None,
            'parent_frame': None,
            'parent_scan': None,
            'ms2_no_precursor': False}


def populate_scan_dict_w_baf_metadata(scan_dict, frames_dict, acquisitionkey_dict, mode):
    """
    Populate spectrum data dictionary with global metadata for BAF files.

    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param frames_dict: A row from the Spectra table in analysis.sqlite database for BAF files.
    :type frames_dict: dict
    :param acquisitionkey_dict: A row from the AcquisitionKey table in analysis.sqlite database for BAF files.
    :type acquisitionkey_dict: dict
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    scan_dict['polarity'] = get_baf_spectrum_polarity(acquisitionkey_dict)
    scan_dict['centroided'] = get_centroid_status(mode)[0]
    scan_dict['retention_time'] = float(frames_dict['Rt']) / 60
    return scan_dict


def populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array):
    """
    Populate spectrum data dictionary with binary data arrays and related metadata.

    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param mz_array: Array containing m/z values.
    :type mz_array: numpy.array
    :param intensity_array: Array containing intensity values.
    :type intensity_array: numpy.array
    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    scan_dict['mz_array'] = mz_array
    scan_dict['intensity_array'] = intensity_array
    scan_dict['total_ion_current'] = sum(intensity_array)
    base_peak_index = np.where(intensity_array == np.max(intensity_array))
    scan_dict['base_peak_mz'] = mz_array[base_peak_index][0].astype(float)
    scan_dict['base_peak_intensity'] = intensity_array[base_peak_index][0].astype(float)
    scan_dict['high_mz'] = float(max(mz_array))
    scan_dict['low_mz'] = float(min(mz_array))
    return scan_dict


def populate_scan_dict_w_ms1(scan_dict, frame):
    """
    Populate spectrum data dictionary with MS1 level metadata.

    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param frame: Frame ID from the Frames table in analysis.tdf/analysis.tsf or Spectra table in analysis.sqlite
    database.
    :type frame: int
    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    scan_dict['scan_type'] = 'MS1 spectrum'
    scan_dict['ms_level'] = 1
    scan_dict['frame'] = frame
    return scan_dict


def populate_scan_dict_w_bbcid_iscid_ms2(scan_dict, frame, schema,  baf_data=None, framemsmsinfo_dict=None):
    """
    Populate spectrum data dictionary with MS2 level metadata when using bbCID or isCID mode.

    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param frame: Frame ID from the Frames table in analysis.tdf/analysis.tsf or Spectra table in analysis.sqlite
        database.
    :type frame: int
    :param schema: Schema as determined by timsconvert.data_input.schema_detection, either TDF, TSF, or BAF.
    :type schema: str
    :param baf_data: baf_data object containing metadata from analysis.sqlite database, defaults to None.
    :type baf_data: timsconvert.classes.TimsconvertBafData | None
    :param framemsmsinfo_dict: A row from the FrameMsmsInfo table in analysis.tsf/analysis.tdf database, defaults to
        None.
    :type framemsmsinfo_dict: dict | None
    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    scan_dict['scan_type'] = 'MSn spectrum'
    scan_dict['ms_level'] = 2
    if schema == 'TSF' or schema == 'TDF':
        scan_dict['collision_energy'] = float(framemsmsinfo_dict['CollisionEnergy'])
    elif schema == 'BAF':
        collision_energy = float(baf_data.analysis['Variables'][(baf_data.analysis['Variables']['Spectrum'] == frame) &
                                                                (baf_data.analysis['Variables']['Variable'] ==
                                                                 5)].to_dict(orient='records')[0]['Value'])
        scan_dict['collision_energy'] = collision_energy
    scan_dict['frame'] = frame
    scan_dict['ms2_no_precursor'] = True
    return scan_dict


def populate_scan_dict_w_baf_ms2(scan_dict, baf_data, frames_dict, frame):
    """
    Populate spectrum data dictionary with MS2 level metadata from BAF files.

    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param baf_data: baf_data object containing metadata from analysis.sqlite database.
    :type baf_data: timsconvert.classes.TimsconvertBafData
    :param frames_dict: A row from the Spectra table in analysis.sqlite database.
    :type frames_dict: dict
    :param frame: Frame ID from the Frames table in analysis.tdf/analysis.tsf or Spectra table in analysis.sqlite.
    :type frame: int
    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    scan_dict['scan_type'] = 'MSn spectrum'
    scan_dict['ms_level'] = 2
    target_mz = float(baf_data.analysis['Variables'][(baf_data.analysis['Variables']['Spectrum'] == frame) &
                                                     (baf_data.analysis['Variables']['Variable'] ==
                                                      7)].to_dict(orient='records')[0]['Value'])
    scan_dict['target_mz'] = target_mz
    isolation_width = float(baf_data.analysis['Variables'][(baf_data.analysis['Variables']['Spectrum'] == frame) &
                                                           (baf_data.analysis['Variables']['Variable'] ==
                                                            8)].to_dict(orient='records')[0]['Value'])
    scan_dict['isolation_lower_offset'] = isolation_width / 2
    scan_dict['isolation_upper_offset'] = isolation_width / 2
    steps_dict = baf_data.analysis['Steps'][baf_data.analysis['Steps']['TargetSpectrum'] ==
                                            frame].to_dict(orient='records')[0]
    scan_dict['selected_ion_mz'] = float(steps_dict['Mass'])
    scan_dict['charge_state'] = baf_data.analysis['Variables'][(baf_data.analysis['Variables']['Spectrum'] == frame) &
                                                               (baf_data.analysis['Variables']['Variable'] ==
                                                                6)].to_dict(orient='records')[0]['Value']
    collision_energy = baf_data.analysis['Variables'][(baf_data.analysis['Variables']['Spectrum'] == frame) &
                                                      (baf_data.analysis['Variables']['Variable'] ==
                                                       5)].to_dict(orient='records')[0]['Value']
    scan_dict['collision_energy'] = collision_energy
    scan_dict['parent_frame'] = int(frames_dict['Parent'])
    return scan_dict


def populate_scan_dict_w_lcms_tsf_tdf_metadata(scan_dict, frames_dict, mode, exclude_mobility=None):
    """
    Populate spectrum data dictionary with global metadata for TDF and TSF files.

    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param frames_dict: A row from the Frames table in analysis.tdf/analysis.tsf database.
    :type frames_dict: dict
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param exclude_mobility: Whether to include mobility data in the output files, defaults to None.
    :type exclude_mobility: bool | None
    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    scan_dict['polarity'] = frames_dict['Polarity']
    scan_dict['centroided'] = get_centroid_status(mode, exclude_mobility)[0]
    # For ddaPASEF, parent frame RT is used because a precursor spectrum is collected over multiple scans.
    scan_dict['retention_time'] = float(frames_dict['Time']) / 60
    return scan_dict


def populate_scan_dict_w_ddapasef_ms2(scan_dict, tdf_data, precursor_dict, pasefframemsmsinfo_dicts):
    """
    Populate spectrum data dictionary with MS2 level metadata when using ddaPASEF mode.

    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param tdf_data: tdf_data object containing metadata from analysis.tdf database.
    :type tdf_data: timsconvert.classes.TimsconvertTdfData
    :param precursor_dict: A row from the Precursor table in analysis.tdf/analysis.tsf database.
    :type precursor_dict: dict
    :param pasefframemsmsinfo_dicts: A row from the PasefFrameMsmsInfo table in analysis.tdf/analysis.tsf database.
    :type pasefframemsmsinfo_dicts: dict
    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    scan_dict['scan_type'] = 'MSn spectrum'
    scan_dict['ms_level'] = 2
    scan_dict['target_mz'] = float(precursor_dict['AverageMz'])
    scan_dict['isolation_lower_offset'] = float(pasefframemsmsinfo_dicts[0]['IsolationWidth']) / 2
    scan_dict['isolation_upper_offset'] = float(pasefframemsmsinfo_dicts[0]['IsolationWidth']) / 2
    scan_dict['selected_ion_mz'] = float(precursor_dict['LargestPeakMz'])
    scan_dict['selected_ion_intensity'] = float(precursor_dict['Intensity'])
    scan_dict['selected_ion_mobility'] = tims_scannum_to_oneoverk0(tdf_data.api, tdf_data.handle,
                                                                   int(precursor_dict['Parent']),
                                                                   np.array([int(precursor_dict['ScanNumber'])]))[0]
    scan_dict['charge_state'] = precursor_dict['Charge']
    scan_dict['collision_energy'] = pasefframemsmsinfo_dicts[0]['CollisionEnergy']
    scan_dict['parent_frame'] = int(precursor_dict['Parent'])
    scan_dict['parent_scan'] = int(precursor_dict['ScanNumber'])
    if not np.isnan(precursor_dict['Charge']):
        scan_dict['selected_ion_ccs'] = tims_oneoverk0_to_ccs_for_mz(tdf_data.api,
                                                                     scan_dict['selected_ion_mobility'],
                                                                     int(precursor_dict['Charge']),
                                                                     float(precursor_dict['LargestPeakMz']))
    return scan_dict


def populate_scan_dict_w_diapasef_ms2(scan_dict, diaframemsmswindows_dict):
    """
    Populate spectrum data dictionary with MS2 level metadata when using diaPASEF mode.

    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param diaframemsmswindows_dict: A row from the DiaFrameMsmsWindows table in analysis.tdf/analysis.tsf database.
    :type diaframemsmswindows_dict: dict
    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    scan_dict['scan_type'] = 'MSn spectrum'
    scan_dict['ms_level'] = 2
    scan_dict['target_mz'] = float(diaframemsmswindows_dict['IsolationMz'])
    scan_dict['isolation_lower_offset'] = float(diaframemsmswindows_dict['IsolationWidth']) / 2
    scan_dict['isolation_upper_offset'] = float(diaframemsmswindows_dict['IsolationWidth']) / 2
    scan_dict['selected_ion_mz'] = float(diaframemsmswindows_dict['IsolationMz'])
    scan_dict['collision_energy'] = diaframemsmswindows_dict['CollisionEnergy']
    return scan_dict


def populate_scan_dict_w_prmpasef_ms2(tdf_data, scan_dict, prmframemsmsinfo_dict, prmtargets_dict):
    """
    Populate spectrum data dictionary with MS2 level metadata when using diaPASEF mode.

    :param tdf_data: tdf_data object containing metadata from analysis.tdf database.
    :type tdf_data: timsconvert.classes.TimsconvertTdfData
    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param prmframemsmsinfo_dict: A row from the PrmFrameMsmsInfo table in analysis.tdf/analysis.tsf database.
    :type prmframemsmsinfo_dict: dict
    :param prmtargets_dict: A row from the PrmTargets table in analysis.tdf/analysis.tsf database.
    :type prmtargets_dict: dict
    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    scan_dict['scan_type'] = 'MSn spectrum'
    scan_dict['ms_level'] = 2
    scan_dict['target_mz'] = float(prmframemsmsinfo_dict['IsolationMz'])
    scan_dict['isolation_lower_offset'] = float(prmframemsmsinfo_dict['IsolationWidth']) / 2
    scan_dict['isolation_upper_offset'] = float(prmframemsmsinfo_dict['IsolationWidth']) / 2
    scan_dict['selected_ion_mz'] = float(prmframemsmsinfo_dict['IsolationMz'])
    scan_dict['selected_ion_mobility'] = float(prmtargets_dict['OneOverK0'])
    scan_dict['charge_state'] = prmtargets_dict['Charge']
    scan_dict['collision_energy'] = prmframemsmsinfo_dict['CollisionEnergy']
    if not np.isnan(prmtargets_dict['Charge']):
        scan_dict['selected_ion_ccs'] = tims_oneoverk0_to_ccs_for_mz(tdf_data.api,
                                                                     scan_dict['selected_ion_mobility'],
                                                                     int(prmtargets_dict['Charge']),
                                                                     float(prmframemsmsinfo_dict['IsolationMz']))
    return scan_dict


def populate_scan_dict_w_maldi_metadata(scan_dict, data, frames_dict, maldiframeinfo_dict, frame, mode):
    """
    Populate spectrum data dictionary with global metadata from MALDI TDF/TSF files.

    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param data: tsf_data or tdf_data object containing metadata from analysis.tsf/analysis.tdf database.
    :type data: timsconvert.classes.TimsconvertTsfData | timsconvert.classes.TimsconvertTdfData
    :param frames_dict: A row from the Frames table in analysis.tdf/analysis.tsf database.
    :type frames_dict: dict
    :param maldiframeinfo_dict: A row from the MaldiFrameInfo table in analysis.tdf/analysis.tsf database.
    :type maldiframeinfo_dict: dict
    :param frame: Frame ID from the Frames table in analysis.tdf/analysis.tsf database.
    :type frame: int
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    """
    scan_dict['coord'] = get_maldi_coords(data, maldiframeinfo_dict)
    scan_dict['polarity'] = frames_dict['Polarity']
    scan_dict['centroided'] = get_centroid_status(mode)[0]
    scan_dict['retention_time'] = 0
    scan_dict['frame'] = frame
    return scan_dict


def populate_scan_dict_w_tsf_ms2(scan_dict, framemsmsinfo_dict, lcms=False):
    """
    Populate spectrum data dictionary with MS2 level metadata from TSF files.

    :param scan_dict: Spectrum data dictionary generated from timsconvert.parse.init_scan_dict().
    :type scan_dict: dict
    :param framemsmsinfo_dict: A row from the FrameMsmsInfo table in analysis.tdf/analysis.tsf database.
    :type framemsmsinfo_dict: dict
    :param lcms: Whether the data is from an lcms dataset, defaults to False
    :type lcms: bool
    :return: Dictionary containing standard spectrum data.
    :rtype: dict
    """
    scan_dict['scan_type'] = 'MSn spectrum'
    scan_dict['ms_level'] = 2
    scan_dict['target_mz'] = float(framemsmsinfo_dict['TriggerMass'])
    scan_dict['isolation_lower_offset'] = float(framemsmsinfo_dict['IsolationWidth']) / 2
    scan_dict['isolation_upper_offset'] = float(framemsmsinfo_dict['IsolationWidth']) / 2
    scan_dict['selected_ion_mz'] = float(framemsmsinfo_dict['TriggerMass'])
    scan_dict['charge_state'] = framemsmsinfo_dict['PrecursorCharge']
    scan_dict['collision_energy'] = framemsmsinfo_dict['CollisionEnergy']
    if lcms:
        scan_dict['parent_frame'] = int(framemsmsinfo_dict['Parent'])
    return scan_dict


def bin_profile_spectrum(mz_array, intensity_array, profile_bins, encoding):
    """
    Bin profile mode spectrum into N number of bins.

    :param mz_array: Array containing m/z values.
    :type mz_array: numpy.array
    :param intensity_array: Array containing intensity values.
    :type intensity_array: numpy.array
    :param profile_bins: Number of bins to bin spectrum to.
    :type profile_bins: int
    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: Tuple of binned_mz_array (np.array) and binned_intensity_array (np.array).
    :rtype: tuple[numpy.array]
    """
    mz_acq_range_lower = float(mz_array[0])
    mz_acq_range_upper = float(mz_array[-1])
    bins = np.linspace(mz_acq_range_lower, mz_acq_range_upper, profile_bins, dtype=get_encoding_dtype(encoding))
    unique_indices, inverse_indices = np.unique(np.digitize(mz_array, bins), return_inverse=True)
    bin_counts = np.bincount(inverse_indices)
    np.place(bin_counts, bin_counts < 1, [1])
    mz_array = np.bincount(inverse_indices, weights=mz_array) / bin_counts
    intensity_array = np.bincount(inverse_indices, weights=intensity_array)
    return mz_array, intensity_array


def extract_baf_spectrum(baf_data, frames_dict, mode, profile_bins, encoding):
    """
    Extract spectrum from BAF data with m/z and intensity arrays. Spectrum can either be centroid or profile mode. If
    "raw" mode is chosen, centroid mode will automatically be used.

    :param baf_data: baf_data object containing metadata from analysis.sqlite database.
    :type baf_data: timsconvert.classes.TimsconvertBafData
    :param frames_dict: A row from the Spectra table in analysis.sqlite database.
    :type frames_dict: dict
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param profile_bins: Number of bins to bin spectrum to.
    :type profile_bins: int
    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: Tuple of mz_array (np.array) and intensity_array (np.array).
    :rtype: tuple[numpy.array]
    """
    if mode == 'raw' or mode == 'centroid':
        mz_array = np.array(read_double(baf_data.api, baf_data.handle, int(frames_dict['LineMzId'])),
                            dtype=get_encoding_dtype(encoding))
        intensity_array = np.array(read_double(baf_data.api, baf_data.handle, int(frames_dict['LineIntensityId'])),
                                   dtype=get_encoding_dtype(encoding))
    elif mode == 'profile':
        mz_array = np.array(read_double(baf_data.api, baf_data.handle, int(frames_dict['ProfileMzId'])),
                            dtype=get_encoding_dtype(encoding))
        intensity_array = np.array(read_double(baf_data.api, baf_data.handle, int(frames_dict['ProfileIntensityId'])),
                                   dtype=get_encoding_dtype(encoding))
        if profile_bins != 0:
            mz_array, intensity_array = bin_profile_spectrum(mz_array, intensity_array, profile_bins, encoding)
    return mz_array, intensity_array


def extract_tsf_spectrum(tsf_data, mode, frame, profile_bins, encoding):
    """
    Extract spectrum from TSF data with m/z and intensity arrays. Spectrum can either be centroid or quasi-profile
    mode. If "raw" mode is chosen, centroid mode will automatically be used. "Centroid" mode uses
    pyTDFSDK.tsf.tsf_read_line_spectrum_v2() method. "Profile" mode uses pyTDFSDK.tsf.tsf_read_profile_spectrum_v2() to
    extrapolate a quasi-profile spectrum from centroid raw data.

    :param tsf_data: tsf_data object containing metadata from analysis.tsf database.
    :type tsf_data: timsconvert.classes.TimsconvertTsfData
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param frame: Frame ID from the Frames table in analysis.tdf/analysis.tsf database.
    :type frame: int
    :param profile_bins: Number of bins to bin spectrum to.
    :type profile_bins: int
    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: Tuple of mz_array (np.array) and intensity_array (np.array).
    :rtype: tuple[numpy.array]
    """
    if mode == 'raw' or mode == 'centroid':
        index_buf, intensity_array = tsf_read_line_spectrum_v2(tsf_data.api, tsf_data.handle, frame)
        mz_array = tsf_index_to_mz(tsf_data.api, tsf_data.handle, frame, index_buf)
    elif mode == 'profile':
        index_buf, intensity_array = tsf_read_profile_spectrum_v2(tsf_data.api, tsf_data.handle, frame)
        intensity_array = np.array(intensity_array, dtype=get_encoding_dtype(encoding))
        mz_array = tsf_index_to_mz(tsf_data.api, tsf_data.handle, frame, index_buf)
        if profile_bins != 0:
            mz_array, intensity_array = bin_profile_spectrum(mz_array, intensity_array, profile_bins, encoding)
    return mz_array, intensity_array


def extract_2d_tdf_spectrum(tdf_data, mode, frame, scan_begin, scan_end, profile_bins, encoding):
    """
    Extract spectrum from TDF data with m/z and intensity arrays. Spectrum can either be centroid or quasi-profile
    mode. "Raw" mode uses pyTDFSDK.tims.tims_read_scans_v2() method, while "centroid" mode uses
    pyTDFSDK.tims.tims_extract_centroided_spectrum_for_frame_v2() method. "Profile" mode uses
    pyTDFSDK.tims.tims_extract_profile_for_frame() to extrapolate a quasi-profile spectrum from centroid raw data.

    :param tdf_data: tdf_data object containing metadata from analysis.tdf database.
    :type tdf_data: timsconvert.classes.TimsconvertTdfData
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param frame: Frame ID from the Frames table in analysis.tdf/analysis.tsf database.
    :type frame: int
    :param scan_begin: Beginning scan number (corresponding to 1/K0 value) within frame.
    :type scan_begin: int
    :param scan_end: Ending scan number (corresponding to 1/K0 value) within frame (non-inclusive).
    :type scan_end: int
    :param profile_bins: Number of bins to bin spectrum to.
    :type profile_bins: int
    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: Tuple of mz_array (np.array) and intensity_array (np.array) or (None, None) if spectra are empty.
    :rtype: tuple[numpy.array | None]
    """
    if mode == 'raw':
        list_of_scans = tims_read_scans_v2(tdf_data.api, tdf_data.handle, frame, scan_begin, scan_end)
        frame_mz_arrays = []
        frame_intensity_arrays = []
        for scan_num in range(scan_begin, scan_end):
            if list_of_scans[scan_num][0].size != 0 \
                    and list_of_scans[scan_num][1].size != 0 \
                    and list_of_scans[scan_num][0].size == list_of_scans[scan_num][1].size:
                mz_array = tims_index_to_mz(tdf_data.api, tdf_data.handle, frame, list_of_scans[scan_num][0])
                intensity_array = list_of_scans[scan_num][1]
                frame_mz_arrays.append(mz_array)
                frame_intensity_arrays.append(intensity_array)
        if frame_mz_arrays and frame_intensity_arrays:
            frames_array = np.stack((np.concatenate(frame_mz_arrays, axis=None),
                                     np.concatenate(frame_intensity_arrays, axis=None)),
                                    axis=-1)
            frames_array = np.unique(frames_array[np.argsort(frames_array[:, 0])], axis=0)
            mz_array = frames_array[:, 0]
            intensity_array = frames_array[:, 1]
            return mz_array, intensity_array
        else:
            return None, None
    elif mode == 'profile':
        index_buf, intensity_array = tims_extract_profile_for_frame(tdf_data.api,
                                                                    tdf_data.handle,
                                                                    frame,
                                                                    scan_begin,
                                                                    scan_end)
        intensity_array = np.array(intensity_array, dtype=get_encoding_dtype(encoding))
        mz_array = tims_index_to_mz(tdf_data.api, tdf_data.handle, frame, index_buf)
        if profile_bins != 0:
            mz_array, intensity_array = bin_profile_spectrum(mz_array, intensity_array, profile_bins, encoding)
    elif mode == 'centroid':
        mz_array, intensity_array = tims_extract_centroided_spectrum_for_frame_v2(tdf_data.api,
                                                                                  tdf_data.handle,
                                                                                  frame,
                                                                                  scan_begin,
                                                                                  scan_end)
        mz_array = np.array(mz_array, dtype=get_encoding_dtype(encoding))
        intensity_array = np.array(intensity_array, dtype=get_encoding_dtype(encoding))
    return mz_array, intensity_array


def extract_3d_tdf_spectrum(tdf_data, frame, scan_begin, scan_end):
    """
    Extract spectrum from TDF data with m/z and intensity arrays. Spectrum can either be centroid or quasi-profile
    mode. "Raw" and "centroid" modes uses pyTDFSDK.tims.tims_read_scans_v2(). "Profile" mode data is not available due
    to the resulting data size.

    :param tdf_data: tdf_data object containing metadata from analysis.tdf database.
    :type tdf_data: timsconvert.classes.TimsconvertTdfData
    :param frame: Frame ID from the Frames table in analysis.tdf/analysis.tsf database.
    :type frame: int
    :param scan_begin: Beginning scan number (corresponding to 1/K0 value) within frame.
    :type scan_begin: int
    :param scan_end: Ending scan number (corresponding to 1/K0 value) within frame (non-inclusive).
    :type scan_end: int
    :return: Tuple of mz_array (np.array), intensity_array (np.array), and mobility_array (np.array) or
        (None, None, None) if spectra are empty.
    :rtype: tuple[numpy.array | None]
    """
    list_of_scans = tims_read_scans_v2(tdf_data.api, tdf_data.handle, frame, scan_begin, scan_end)
    frame_mz_arrays = []
    frame_intensity_arrays = []
    frame_mobility_arrays = []
    if scan_begin != 0:
        scan_end = scan_end - scan_begin
        scan_begin = 0
    for scan_num in range(scan_begin, scan_end):
        if list_of_scans[scan_num][0].size != 0 \
                and list_of_scans[scan_num][1].size != 0 \
                and list_of_scans[scan_num][0].size == list_of_scans[scan_num][1].size:
            mz_array = tims_index_to_mz(tdf_data.api, tdf_data.handle, frame, list_of_scans[scan_num][0])
            intensity_array = list_of_scans[scan_num][1]
            mobility = tims_scannum_to_oneoverk0(tdf_data.api, tdf_data.handle, frame, np.array([scan_num]))[0]
            mobility_array = np.repeat(mobility, mz_array.size)
            frame_mz_arrays.append(mz_array)
            frame_intensity_arrays.append(intensity_array)
            frame_mobility_arrays.append(mobility_array)
    if frame_mz_arrays and frame_intensity_arrays and frame_mobility_arrays:
        frames_array = np.stack((np.concatenate(frame_mz_arrays, axis=None),
                                 np.concatenate(frame_intensity_arrays, axis=None),
                                 np.concatenate(frame_mobility_arrays, axis=None)),
                                axis=-1)
        frames_array = np.unique(frames_array[np.argsort(frames_array[:, 0])], axis=0)
        mz_array = frames_array[:, 0]
        intensity_array = frames_array[:, 1]
        mobility_array = frames_array[:, 2]
        return mz_array, intensity_array, mobility_array
    else:
        return None, None, None


def extract_ddapasef_precursor_spectrum(tdf_data, pasefframemsmsinfo_dicts, mode, profile_bins, encoding):
    """
    Extract spectrum from TDF data with m/z and intensity arrays. Spectrum can either be centroid or quasi-profile
    mode. "Raw" mode uses pyTDFSDK.tims.tims_read_scans_v2() method, while "centroid" mode uses
    pyTDFSDK.tims.tims_extract_centroided_spectrum_for_frame_v2() method. "Profile" mode uses
    pyTDFSDK.tims.tims_extract_profile_for_frame() to extrapolate a quasi-profile spectrum from centroid raw data.

    :param tdf_data: tdf_data object containing metadata from analysis.tdf database.
    :type tdf_data: timsconvert.classes.TimsconvertTdfData
    :param pasefframemsmsinfo_dicts: A row from the PasefFrameMsmsInfo table in analysis.tdf database.
    :type pasefframemsmsinfo_dicts: dict
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param profile_bins: Number of bins to bin spectrum to.
    :type profile_bins: int
    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: Tuple of mz_array (np.array) and intensity_array (np.array) or (None, None) if spectra are empty.
    :rtype: tuple[numpy.array | None
    """
    pasef_mz_arrays = []
    pasef_intensity_arrays = []
    for pasef_dict in pasefframemsmsinfo_dicts:
        scan_begin = int(pasef_dict['ScanNumBegin'])
        scan_end = int(pasef_dict['ScanNumEnd'])
        mz_array, intensity_array = extract_2d_tdf_spectrum(tdf_data,
                                                            mode,
                                                            int(pasef_dict['Frame']),
                                                            scan_begin,
                                                            scan_end,
                                                            profile_bins,
                                                            encoding)
        if mz_array.size != 0 and intensity_array.size != 0 and mz_array.size == intensity_array.size:
            pasef_mz_arrays.append(mz_array)
            pasef_intensity_arrays.append(intensity_array)
    if pasef_mz_arrays and pasef_intensity_arrays:
        pasef_array = np.stack((np.concatenate(pasef_mz_arrays, axis=None),
                                np.concatenate(pasef_intensity_arrays, axis=None)),
                               axis=-1)
        pasef_array = np.unique(pasef_array[np.argsort(pasef_array[:, 0])], axis=0)

        mz_acq_range_lower = float(tdf_data.analysis['GlobalMetadata']['MzAcqRangeLower'])
        mz_acq_range_upper = float(tdf_data.analysis['GlobalMetadata']['MzAcqRangeUpper'])
        bin_size = 0.005
        bins = np.arange(mz_acq_range_lower, mz_acq_range_upper, bin_size,
                         dtype=get_encoding_dtype(encoding))

        unique_indices, inverse_indices = np.unique(np.digitize(pasef_array[:, 0], bins),
                                                    return_inverse=True)
        bin_counts = np.bincount(inverse_indices)
        np.place(bin_counts, bin_counts < 1, [1])

        mz_array = np.bincount(inverse_indices, weights=pasef_array[:, 0]) / bin_counts
        intensity_array = np.bincount(inverse_indices, weights=pasef_array[:, 1])
        return mz_array, intensity_array
    else:
        return None, None


def parse_lcms_baf(baf_data, frame_start, frame_stop, mode, ms2_only, profile_bins, encoding):
    """
    Parse group of frames from LC-MS(/MS) data from Bruker BAF files acquired in MS1 only, Auto MS/MS, MRM MS/MS, isCID
    MS/MS, or bbCID MS/MS mode in otofControl.

    :param baf_data: baf_data object containing metadata from analysis.sqlite database.
    :type baf_data: timsconvert.classes.TimsconvertBafData
    :param frame_start: Beginning frame number.
    :type frame_start: int
    :param frame_stop: Ending frame number (non-inclusive).
    :type frame_stop: int
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param ms2_only: Whether to include MS1 data in the output files.
    :type ms2_only: bool
    :param profile_bins: Number of bins to bin spectrum to.
    :type profile_bins: int
    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: Tuple of (list of dictionaries containing MS1 spectrum data, list of dictionaries containing MS/MS
    spectrum data).
    :rtype: tuple[list[dict]]
    """
    list_of_parent_scans = []
    list_of_product_scans = []

    for frame in range(frame_start, frame_stop):
        scan_dict = init_scan_dict()
        frames_dict = baf_data.analysis['Spectra'][baf_data.analysis['Spectra']['Id'] ==
                                                   frame].to_dict(orient='records')[0]
        acquisitionkey_dict = baf_data.analysis['AcquisitionKeys'][baf_data.analysis['AcquisitionKeys']['Id'] ==
                                                                   frames_dict['AcquisitionKey']].to_dict(orient='records')[0]
        scan_dict = populate_scan_dict_w_baf_metadata(scan_dict, frames_dict, acquisitionkey_dict, mode)

        mz_array, intensity_array = extract_baf_spectrum(baf_data, frames_dict, mode, profile_bins, encoding)
        if mz_array.size != 0 and intensity_array.size != 0 and mz_array.size == intensity_array.size:
            scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
            # MS1
            if int(acquisitionkey_dict['ScanMode']) == 0 and not ms2_only:
                scan_dict = populate_scan_dict_w_ms1(scan_dict, frame)
                list_of_parent_scans.append(scan_dict)
            # Auto MS/MS and MRM MS/MS
            elif int(acquisitionkey_dict['ScanMode']) == 2:
                scan_dict = populate_scan_dict_w_baf_ms2(scan_dict, baf_data, frames_dict, frame)
                list_of_product_scans.append(scan_dict)
            # isCID MS/MS
            elif int(acquisitionkey_dict['ScanMode']) == 4:
                scan_dict = populate_scan_dict_w_bbcid_iscid_ms2(scan_dict, frame, 'BAF', baf_data=baf_data)
                list_of_parent_scans.append(scan_dict)
            # bbCID MS/MS
            elif int(acquisitionkey_dict['ScanMode']) == 5:
                scan_dict = populate_scan_dict_w_bbcid_iscid_ms2(scan_dict, frame, 'BAF', baf_data=baf_data)
                list_of_parent_scans.append(scan_dict)
    return list_of_parent_scans, list_of_product_scans


def parse_lcms_tsf(tsf_data, frame_start, frame_stop, mode, ms2_only, profile_bins, encoding):
    """
    Parse group of frames from LC-MS(/MS) data from Bruker TSF files acquired in Auto MS/MS mode MS1 only, Auto MS/MS,
    MRM MS/MS, or bbCID MS/MS mode in timsControl.

    :param tsf_data: tsf_data object containing metadata from analysis.tsf database.
    :type tsf_data: timsconvert.classes.TimsconvertTsfData
    :param frame_start: Beginning frame number.
    :type frame_start: int
    :param frame_stop: Ending frame number (non-inclusive).
    :type frame_stop: int
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param ms2_only: Whether to include MS1 data in the output files.
    :type ms2_only: bool
    :param profile_bins: Number of bins to bin spectrum to.
    :type profile_bins: int
    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: Tuple of (list of dictionaries containing MS1 spectrum data, list of dictionaries containing MS/MS
        spectrum data).
    :rtype: tuple[list[dict]]
    """
    list_of_parent_scans = []
    list_of_product_scans = []

    for frame in range(frame_start, frame_stop):
        scan_dict = init_scan_dict()
        frames_dict = tsf_data.analysis['Frames'][tsf_data.analysis['Frames']['Id'] ==
                                                  frame].to_dict(orient='records')[0]
        scan_dict = populate_scan_dict_w_lcms_tsf_tdf_metadata(scan_dict, frames_dict, mode, exclude_mobility=None)

        mz_array, intensity_array = extract_tsf_spectrum(tsf_data, mode, frame, profile_bins, encoding)
        if mz_array.size != 0 and intensity_array.size != 0 and mz_array.size == intensity_array.size:
            scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
            if int(frames_dict['MsMsType']) in MSMS_TYPE_CATEGORY['ms1'] and not ms2_only:
                scan_dict = populate_scan_dict_w_ms1(scan_dict, frame)
                list_of_parent_scans.append(scan_dict)
            elif int(frames_dict['MsMsType']) in MSMS_TYPE_CATEGORY['ms2']:
                framemsmsinfo_dict = tsf_data.analysis['FrameMsMsInfo'][tsf_data.analysis['FrameMsMsInfo']['Frame'] ==
                                                                        frame].to_dict(orient='records')[0]
                if int(frames_dict['ScanMode']) == 1:
                    scan_dict = populate_scan_dict_w_tsf_ms2(scan_dict, framemsmsinfo_dict, lcms=True)
                    list_of_product_scans.append(scan_dict)
                elif int(frames_dict['ScanMode']) == 4:
                    scan_dict = populate_scan_dict_w_bbcid_iscid_ms2(scan_dict,
                                                                     frame,
                                                                     'TSF',
                                                                     framemsmsinfo_dict=framemsmsinfo_dict)
                    list_of_parent_scans.append(scan_dict)
                elif int(frames_dict['ScanMode']) == 2:
                    scan_dict = populate_scan_dict_w_tsf_ms2(scan_dict, framemsmsinfo_dict)
                    list_of_parent_scans.append(scan_dict)
    return list_of_parent_scans, list_of_product_scans


def parse_lcms_tdf(tdf_data, frame_start, frame_stop, mode, ms2_only, exclude_mobility, profile_bins, encoding):
    """
    Parse group of frames from LC-MS(/MS) data from Bruker TDF files acquired in MS1 only, ddaPASEF MS/MS, diaPASEF
    MS/MS, bbCID MS/MS, MRM MS/MS, or prmPASEF MS/MS mode in timsControl.

    :param tdf_data: tdf_data object containing metadata from analysis.tdf database.
    :type tdf_data: timsconvert.classes.TimsconvertTdfData
    :param frame_start: Beginning frame number.
    :type frame_start: int
    :param frame_stop: Ending frame number (non-inclusive).
    :type frame_stop: int
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param ms2_only: Whether to include MS1 data in the output files.
    :type ms2_only: bool
    :param exclude_mobility: Whether to include mobility data in the output files, defaults to None.
    :type exclude_mobility: bool | None
    :param profile_bins: Number of bins to bin spectrum to.
    :type profile_bins: int
    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: Tuple of (list of dictionaries containing MS1 spectrum data, list of dictionaries containing MS/MS
        spectrum data).
    :rtype: tuple[list[dict]]
    """
    list_of_parent_scans = []
    list_of_product_scans = []
    exclude_mobility = get_centroid_status(mode, exclude_mobility)[1]

    # Frame start and frame stop will only be MS1 frames; MS2 frames cannot be used as frame_start and frame_stop.
    for frame in range(frame_start, frame_stop):
        # Parse MS1 frame(s).
        frames_dict = tdf_data.analysis['Frames'][tdf_data.analysis['Frames']['Id'] ==
                                                  frame].to_dict(orient='records')[0]

        if int(frames_dict['MsMsType']) in MSMS_TYPE_CATEGORY['ms1'] and not ms2_only:
            scan_dict = init_scan_dict()
            scan_dict = populate_scan_dict_w_lcms_tsf_tdf_metadata(scan_dict, frames_dict, mode, exclude_mobility)
            scan_dict = populate_scan_dict_w_ms1(scan_dict, frame)
            if not exclude_mobility:
                mz_array, intensity_array, mobility_array = extract_3d_tdf_spectrum(tdf_data,
                                                                                    frame,
                                                                                    0,
                                                                                    int(frames_dict['NumScans']))
            elif exclude_mobility:
                mz_array, intensity_array = extract_2d_tdf_spectrum(tdf_data,
                                                                    mode,
                                                                    frame,
                                                                    0,
                                                                    int(frames_dict['NumScans']),
                                                                    profile_bins,
                                                                    encoding)
            if mz_array.size != 0 \
                    and intensity_array.size != 0 \
                    and mz_array.size == intensity_array.size \
                    and mz_array is not None \
                    and intensity_array is not None:
                scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
                if not exclude_mobility and mobility_array.size != 0 and mobility_array is not None:
                    scan_dict['mobility_array'] = mobility_array
                list_of_parent_scans.append(scan_dict)

            # This block only runs if frame_stop - frame_start > 1, meaning MS/MS scans are detected.
            if frame_stop - frame_start > 1:
                # Parse frames with ddaPASEF spectra for precursors.
                if int(frames_dict['ScanMode']) == 8 and int(frames_dict['MsMsType']) == 0:
                    precursor_dicts = tdf_data.analysis['Precursors'][tdf_data.analysis['Precursors']['Parent'] ==
                                                                      frame].to_dict(orient='records')
                    for precursor_dict in precursor_dicts:
                        scan_dict = init_scan_dict()
                        scan_dict = populate_scan_dict_w_lcms_tsf_tdf_metadata(scan_dict,
                                                                               frames_dict,
                                                                               mode,
                                                                               exclude_mobility)
                        pasefframemsmsinfo_dicts = tdf_data.analysis['PasefFrameMsMsInfo'][tdf_data.analysis['PasefFrameMsMsInfo']['Precursor'] ==
                                                                                           precursor_dict['Id']].to_dict(orient='records')
                        mz_array, intensity_array = extract_ddapasef_precursor_spectrum(tdf_data,
                                                                                        pasefframemsmsinfo_dicts,
                                                                                        mode,
                                                                                        profile_bins,
                                                                                        encoding)
                        if mz_array is not None and intensity_array is not None:
                            scan_dict = populate_scan_dict_w_ddapasef_ms2(scan_dict,
                                                                          tdf_data,
                                                                          precursor_dict,
                                                                          pasefframemsmsinfo_dicts)
                            scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
                            list_of_product_scans.append(scan_dict)
        # Parse frames with diaPASEF spectra.
        elif int(frames_dict['ScanMode']) == 9 and int(frames_dict['MsMsType']) == 9:
            diaframemsmsinfo_dict = tdf_data.analysis['DiaFrameMsMsInfo'][tdf_data.analysis['DiaFrameMsMsInfo']['Frame'] ==
                                                                          frame].to_dict(orient='records')[0]
            diaframemsmswindows_dicts = tdf_data.analysis['DiaFrameMsMsWindows'][tdf_data.analysis['DiaFrameMsMsWindows']['WindowGroup'] ==
                                                                                 diaframemsmsinfo_dict['WindowGroup']].to_dict(orient='records')

            for diaframemsmswindows_dict in diaframemsmswindows_dicts:
                scan_dict = init_scan_dict()
                scan_dict = populate_scan_dict_w_lcms_tsf_tdf_metadata(scan_dict,
                                                                       frames_dict,
                                                                       mode,
                                                                       exclude_mobility)

                if not exclude_mobility:
                    mz_array, intensity_array, mobility_array = extract_3d_tdf_spectrum(tdf_data,
                                                                                        frame,
                                                                                        int(diaframemsmswindows_dict['ScanNumBegin']),
                                                                                        int(diaframemsmswindows_dict['ScanNumEnd']))
                elif exclude_mobility:
                    mz_array, intensity_array = extract_2d_tdf_spectrum(tdf_data,
                                                                        mode,
                                                                        frame,
                                                                        int(diaframemsmswindows_dict['ScanNumBegin']),
                                                                        int(diaframemsmswindows_dict['ScanNumEnd']),
                                                                        profile_bins,
                                                                        encoding)
                if mz_array.size != 0 \
                        and intensity_array.size != 0 \
                        and mz_array.size == intensity_array.size \
                        and mz_array is not None \
                        and intensity_array is not None:
                    scan_dict = populate_scan_dict_w_diapasef_ms2(scan_dict, diaframemsmswindows_dict)
                    scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
                    if not exclude_mobility and mobility_array.size != 0 and mobility_array is not None:
                        scan_dict['mobility_array'] = mobility_array
                    list_of_parent_scans.append(scan_dict)
        # Parse frames with bbCID spectra.
        elif int(frames_dict['ScanMode']) == 4 and int(frames_dict['MsMsType']) == 2:
            scan_dict = init_scan_dict()
            scan_dict = populate_scan_dict_w_lcms_tsf_tdf_metadata(scan_dict, frames_dict, mode, exclude_mobility)
            framemsmsinfo_dict = tdf_data.analysis['FrameMsMsInfo'][tdf_data.analysis['FrameMsMsInfo']['Frame'] ==
                                                                    frame].to_dict(orient='records')[0]
            scan_dict = populate_scan_dict_w_bbcid_iscid_ms2(scan_dict,
                                                             frame,
                                                             'TDF',
                                                             framemsmsinfo_dict=framemsmsinfo_dict)
            if not exclude_mobility:
                mz_array, intensity_array, mobility_array = extract_3d_tdf_spectrum(tdf_data,
                                                                                    frame,
                                                                                    0,
                                                                                    int(frames_dict['NumScans']))
            elif exclude_mobility:
                mz_array, intensity_array = extract_2d_tdf_spectrum(tdf_data,
                                                                    mode,
                                                                    frame,
                                                                    0,
                                                                    int(frames_dict['NumScans']),
                                                                    profile_bins,
                                                                    encoding)
            if mz_array.size != 0 \
                    and intensity_array.size != 0 \
                    and mz_array.size == intensity_array.size \
                    and mz_array is not None \
                    and intensity_array is not None:
                scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
                if not exclude_mobility and mobility_array.size != 0 and mobility_array is not None:
                    scan_dict['mobility_array'] = mobility_array
                list_of_parent_scans.append(scan_dict)
        # Parse frames with MRM spectra.
        elif int(frames_dict['ScanMode']) == 2 and int(frames_dict['MsMsType']) == 2:
            scan_dict = init_scan_dict()
            scan_dict = populate_scan_dict_w_lcms_tsf_tdf_metadata(scan_dict, frames_dict, mode, exclude_mobility)
            framemsmsinfo_dict = tdf_data.analysis['FrameMsMsInfo'][tdf_data.analysis['FrameMsMsInfo']['Frame'] ==
                                                                    frame].to_dict(orient='records')[0]
            mz_array, intensity_array = extract_2d_tdf_spectrum(tdf_data,
                                                                mode,
                                                                frame,
                                                                0,
                                                                int(frames_dict['NumScans']),
                                                                profile_bins,
                                                                encoding)
            if mz_array.size != 0 \
                    and intensity_array.size != 0 \
                    and mz_array.size == intensity_array.size \
                    and mz_array is not None \
                    and intensity_array is not None:
                # lcms set as False since MRM MS/MS spectra do not have a parent frame.
                scan_dict = populate_scan_dict_w_tsf_ms2(scan_dict, framemsmsinfo_dict, lcms=False)
                scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
                list_of_parent_scans.append(scan_dict)
        # Parse frames with prm-PASEF spectra.
        elif int(frames_dict['ScanMode']) == 10 and int(frames_dict['MsMsType']) == 10:
            scan_dict = init_scan_dict()
            scan_dict = populate_scan_dict_w_lcms_tsf_tdf_metadata(scan_dict, frames_dict, mode, exclude_mobility)
            prmframemsmsinfo_dict = tdf_data.analysis['PrmFrameMsMsInfo'][tdf_data.analysis['PrmFrameMsMsInfo']['Frame'] ==
                                                                          frame].to_dict(orient='records')[0]
            prmtargets_dict = tdf_data.analysis['PrmTargets'][tdf_data.analysis['PrmTargets']['Id'] ==
                                                              int(prmframemsmsinfo_dict['Target'])].to_dict(orient='records')[0]
            mz_array, intensity_array = extract_2d_tdf_spectrum(tdf_data,
                                                                mode,
                                                                frame,
                                                                int(prmframemsmsinfo_dict['ScanNumBegin']),
                                                                int(prmframemsmsinfo_dict['ScanNumEnd']),
                                                                profile_bins,
                                                                encoding)
            if mz_array.size != 0 \
                    and intensity_array.size != 0 \
                    and mz_array.size == intensity_array.size \
                    and mz_array is not None \
                    and intensity_array is not None:
                scan_dict = populate_scan_dict_w_prmpasef_ms2(tdf_data,
                                                              scan_dict,
                                                              prmframemsmsinfo_dict,
                                                              prmtargets_dict)
                scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
                list_of_parent_scans.append(scan_dict)
    return list_of_parent_scans, list_of_product_scans


def parse_maldi_plate_map(plate_map_filename):
    """
    Parse a MALDI plate map from a CSV file without a column header or row index.

    :param plate_map_filename: Path to the MALDI plate map in CSV format.
    :type plate_map_filename: str
    :return: Dictionary containing standard MTP spot names as the key and spot label/category/condition as the value.
    :rtype: dict
    """
    plate_map = pd.read_csv(plate_map_filename, header=None)
    plate_dict = {}
    for index, row in plate_map.iterrows():
        for count, value in enumerate(row, start=1):
            plate_dict[chr(index + 65) + str(count)] = value
    return plate_dict


def parse_maldi_tsf(tsf_data, frame_start, frame_stop, mode, ms2_only, profile_bins, encoding):
    """
    Parse group of frames from MALDI-MS(/MS) data from Bruker TSF files acquired in MS1 only, MS/MS, or bbCID MS/MS
    mode in timsControl.

    :param tsf_data: tsf_data object containing metadata from analysis.tsf database.
    :type tsf_data: timsconvert.classes.TimsconvertTsfData
    :param frame_start: Beginning frame number.
    :type frame_start: int
    :param frame_stop: Ending frame number (non-inclusive).
    :type frame_stop: int
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param ms2_only: Whether to include MS1 data in the output files.
    :type ms2_only: bool
    :param profile_bins: Number of bins to bin spectrum to.
    :type profile_bins: int
    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: List of dictionaries containing spectrum data.
    :rtype: list[dict]
    """
    list_of_scan_dicts = []

    for frame in range(frame_start, frame_stop):
        scan_dict = init_scan_dict()
        frames_dict = tsf_data.analysis['Frames'][tsf_data.analysis['Frames']['Id'] ==
                                                  frame].to_dict(orient='records')[0]
        maldiframeinfo_dict = tsf_data.analysis['MaldiFrameInfo'][tsf_data.analysis['MaldiFrameInfo']['Frame'] ==
                                                                  frame].to_dict(orient='records')[0]
        scan_dict = populate_scan_dict_w_maldi_metadata(scan_dict,
                                                        tsf_data,
                                                        frames_dict,
                                                        maldiframeinfo_dict,
                                                        frame,
                                                        mode)

        mz_array, intensity_array = extract_tsf_spectrum(tsf_data, mode, frame, profile_bins, encoding)
        if mz_array.size != 0 and intensity_array.size != 0 and mz_array.size == intensity_array.size:
            scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
            if int(frames_dict['MsMsType']) in MSMS_TYPE_CATEGORY['ms1'] and not ms2_only:
                scan_dict = populate_scan_dict_w_ms1(scan_dict, frame)
                list_of_scan_dicts.append(scan_dict)
            elif int(frames_dict['MsMsType']) in MSMS_TYPE_CATEGORY['ms2']:
                framemsmsinfo_dict = tsf_data.analysis['FrameMsMsInfo'][tsf_data.analysis['FrameMsMsInfo']['Frame'] ==
                                                                        maldiframeinfo_dict['Frame']].to_dict(orient='records')[0]
                scan_dict = populate_scan_dict_w_tsf_ms2(scan_dict, framemsmsinfo_dict)
                list_of_scan_dicts.append(scan_dict)
    return list_of_scan_dicts


def parse_maldi_tdf(tdf_data, frame_start, frame_stop, mode, ms2_only, exclude_mobility, profile_bins, encoding):
    """
    Parse group of frames from MALDI-MS(/MS) data from Bruker TDF files acquired in MS1 only, MS/MS, or bbCID MS/MS
    mode in timsControl.

    :param tdf_data: tdf_data object containing metadata from analysis.tdf database.
    :type tdf_data: timsconvert.classes.TimsconvertTdfData
    :param frame_start: Beginning frame number.
    :type frame_start: int
    :param frame_stop: Ending frame number (non-inclusive).
    :type frame_stop: int
    :param mode: Mode command line parameter, either "profile", "centroid", or "raw".
    :type mode: str
    :param ms2_only: Whether to include MS1 data in the output files.
    :type ms2_only: bool
    :param exclude_mobility: Whether to include mobility data in the output files, defaults to None.
    :type exclude_mobility: bool | None
    :param profile_bins: Number of bins to bin spectrum to.
    :type profile_bins: int
    :param encoding: Encoding command line parameter, either "64" or "32".
    :type encoding: int
    :return: List of dictionaries containing spectrum data.
    :rtype: list[dict]
    """
    list_of_scan_dicts = []
    exclude_mobility = get_centroid_status(mode, exclude_mobility)[1]

    for frame in range(frame_start, frame_stop):
        scan_dict = init_scan_dict()
        frames_dict = tdf_data.analysis['Frames'][tdf_data.analysis['Frames']['Id'] ==
                                                  frame].to_dict(orient='records')[0]
        maldiframeinfo_dict = tdf_data.analysis['MaldiFrameInfo'][tdf_data.analysis['MaldiFrameInfo']['Frame'] ==
                                                                  frame].to_dict(orient='records')[0]

        scan_dict = populate_scan_dict_w_maldi_metadata(scan_dict,
                                                        tdf_data,
                                                        frames_dict,
                                                        maldiframeinfo_dict,
                                                        frame,
                                                        mode)

        if int(frames_dict['MsMsType']) in MSMS_TYPE_CATEGORY['ms1'] and not ms2_only:
            scan_dict['scan_type'] = 'MS1 spectrum'
            scan_dict['ms_level'] = 1
            if not exclude_mobility:
                mz_array, intensity_array, mobility_array = extract_3d_tdf_spectrum(tdf_data,
                                                                                    frame,
                                                                                    0,
                                                                                    int(frames_dict['NumScans']))
            elif exclude_mobility:
                mz_array, intensity_array = extract_2d_tdf_spectrum(tdf_data,
                                                                    mode,
                                                                    frame,
                                                                    0,
                                                                    int(frames_dict['NumScans']),
                                                                    profile_bins,
                                                                    encoding)

            if mz_array.size != 0 \
                    and intensity_array.size != 0 \
                    and mz_array.size == intensity_array.size \
                    and mz_array is not None \
                    and intensity_array is not None:
                scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
                if not exclude_mobility and mobility_array.size != 0 and mobility_array is not None:
                    scan_dict['mobility_array'] = mobility_array
                list_of_scan_dicts.append(scan_dict)
        elif int(frames_dict['MsMsType']) in MSMS_TYPE_CATEGORY['ms2']:
            framemsmsinfo_dict = tdf_data.analysis['FrameMsMsInfo'][tdf_data.analysis['FrameMsMsInfo']['Frame'] ==
                                                                    maldiframeinfo_dict['Frame']].to_dict(orient='records')[0]
            if not exclude_mobility:
                mz_array, intensity_array, mobility_array = extract_3d_tdf_spectrum(tdf_data,
                                                                                    frame,
                                                                                    0,
                                                                                    int(frames_dict['NumScans']))
            elif exclude_mobility:
                mz_array, intensity_array = extract_2d_tdf_spectrum(tdf_data,
                                                                    mode,
                                                                    frame,
                                                                    0,
                                                                    int(frames_dict['NumScans']),
                                                                    profile_bins,
                                                                    encoding)

            if mz_array.size != 0 \
                    and intensity_array.size != 0 \
                    and mz_array.size == intensity_array.size \
                    and mz_array is not None \
                    and intensity_array is not None:
                scan_dict = populate_scan_dict_w_spectrum_data(scan_dict, mz_array, intensity_array)
                if not exclude_mobility and mobility_array.size != 0 and mobility_array is not None:
                    scan_dict['mobility_array'] = mobility_array
                scan_dict = populate_scan_dict_w_tsf_ms2(scan_dict, framemsmsinfo_dict)
                list_of_scan_dicts.append(scan_dict)
    return list_of_scan_dicts
