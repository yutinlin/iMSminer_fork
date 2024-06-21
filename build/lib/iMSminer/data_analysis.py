# -*- coding: utf-8 -*-
"""
iMSminer beta
@author: Yu Tin Lin (yutinlin@stanford.edu)
@author: Haohui Bao (susanab20020911@gmail.com)
@author: Troy R. Scoggins IV (t.scoggins@ufl.edu)
@author: Boone M. Prentice (booneprentice@ufl.chem.edu)
License: Apache-2.0 
"""
import os
from matplotlib.gridspec import GridSpec
from matplotlib.colors import ListedColormap, Normalize
from matplotlib.cm import ScalarMappable
from matplotlib.lines import Line2D
from sklearn.manifold import TSNE
from sklearn.cluster import KMeans
from sklearn.linear_model import LinearRegression
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.preprocessing import PolynomialFeatures
from statannotations.Annotator import Annotator
import math
import seaborn as sns
import subprocess
from itertools import permutations, combinations
from statsmodels.formula.api import ols
import statsmodels.api as sm
import seaborn
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import cv2
import numpy as np
import pandas as pd
import gc
os.chdir("/home/yutinlin/workspace/iMSminer")
from assorted_functions import make_image_1c, make_image_1c_njit, max_normalize, clustering_in_embedding, plot_image_at_point, gromov_trick, significance, draw_ROI, repel_labels, str2bool, add_jitter
from numba import jit
import psutil

class DataAnalysis:
    """
    Performs data analysis on preprocessed intensity matrix and coordinate arrays. Mass alignment function wasa refactored from the python module msalign (https://github.com/lukasz-migas/msalign)

    Attributes
    ----------
    data_dir : str, user input
        Path pointing to directory containing preprocessed data
    fig_ratio : str, user input
        Text:figure ratio for rendered figures from options `small`, `medium`, and `large`
    ROI_num : int, user input
        Number of ROIs in dataset
    ROIs : str, user input
        ROI annotations from left to right, top to bottom
    ion_type : str, user input
        Ion types of MS1 hits
    mass_diff : float, user input
        Mass differences of ion types from monoisotopic neutural
    MS1_db_path : str, user input
        Local file path of MS1 database
    mz_col : int, user input
        Number denoting column in database corresponding to exact mass of monoisotopic neutral 
    """
    
    def __init__(
            self
            ):
        detect_gpu = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if detect_gpu.returncode == 0:
            print("GPU Detected:\n", detect_gpu.stdout.strip())

            test_library = ['cupy', 'cudf', 'cuml']
            for lib in test_library:
                try:
                    __import__(lib)
                    print(
                        f"{lib.capitalize()} is installed and imported successfully!")
                    self.gpu = True
                except ImportError:
                    print(
                        f"{lib.capitalize()} is not installed or could not be imported.")
                    self.gpu = False
                    break
        else:
            print("No GPU detected or NVIDIA driver not installed.")
            self.gpu = False

        try:
            __import__("numba")
            print(f"Numba is installed and imported successfully!")
            self.jit = True
        except:
            print(f"Numba is not installed or could not be imported.")
            self.jit = False

        self.data_dir = input("What is your image dataset directory? ")

        fig_ratio_chosen = False
        while not fig_ratio_chosen:
            fig_ratio = input(
                "Render figures with `small`, `medium`, or `large` ratio? ")
            if fig_ratio == "small":
                self.fig_ratio = 10
                fig_ratio_chosen = True
            elif fig_ratio == "medium":
                self.fig_ratio = 12.5
                fig_ratio_chosen = True
            elif fig_ratio == "large":
                self.fig_ratio = 15
                fig_ratio_chosen = True
            else:
                fig_ratio_chosen = False
                print("Select one of the options.")

    def load_preprocessed_data(
            self
            ):
        """
        import preprocessed intensity matrix and coordinate arrays, perform ROI annotation and selection, and store information for further data analysis
        """

        datasets = os.listdir(self.data_dir)
        datasets = np.asarray(datasets)[(np.char.find(
            datasets, "coords") == -1) & (np.char.find(datasets, "csv") != -1)]

        self._df_pixel_all = pd.DataFrame()
        ROI_edge = pd.DataFrame()

        for rep, dataset in enumerate(datasets):
            print(f"Importing {dataset}.")
            df_build = pd.read_csv(f"{self.data_dir}/{dataset}")
            print(f"Finished importing {dataset}.")

            df_build.rename(columns={'Unnamed: 0': 'mz'}, inplace=True)
            df_build = df_build.T
            if not hasattr(self, "_mz"):
                self._mz = df_build.loc['mz']
                self.mz = self._mz.copy()
            df_build.drop('mz', inplace=True)

            df_coords = pd.read_csv(
                f"{self.data_dir}/{dataset[:-4]}_coords.csv")
            df_coords.rename(columns={'0': 'x', '1': 'y'}, inplace=True)
            df_coords['x'] = df_coords['x'] - np.min(df_coords['x']) + 1
            df_coords['y'] = df_coords['y'] - np.min(df_coords['y']) + 1

            df_build.reset_index(drop=True, inplace=True)
            df_coords.reset_index(drop=True, inplace=True)

            df_build = pd.concat([df_build, df_coords[['x', 'y']]], axis=1)

            img_array_1c = make_image_1c(data_2darray=pd.concat([pd.Series(np.sum(
                df_build.iloc[:, :-2], axis=1)), df_build.iloc[:, -2:]], axis=1).to_numpy())
            self.img_array_1c = img_array_1c
            self.ROI_num = int(input("How many ROIs are you analyzing? "))
            ROIs = input(
                "How to name your ROIs, from left to right, top to bottom? Separate ROI names by one space.")
            ROIs = ROIs.split(" ")

            ROI_exit = False
            while not ROI_exit:
                ROI_dim_array = np.empty((0, 4))
                for ROI in ROIs:
                    ROI_dim = cv2.selectROI('ROI_selection', img_array_1c[0], showCrosshair=True)
                    ROI_dim = np.asarray(ROI_dim)
                    ROI_dim_array = np.append(ROI_dim_array, ROI_dim.reshape((1,-1)), axis=0)
                cv2.destroyAllWindows()
                ROI_sele = input("Keep ROI selecction? (yes/no) ")
                if ROI_sele == "yes":
                    ROI_exit = True
                else:
                    ROI_exit = False

            df_build['ROI'] = 'placeholder'
            df_build['replicate'] = rep

            for i, ROI in enumerate(ROIs):
                print(f"Select ROI {i}, labeled {ROI}.")
                bottom = ROI_dim_array[i][1]
                top = ROI_dim_array[i][1] + ROI_dim_array[i][3]
                left = ROI_dim_array[i][0]
                right = ROI_dim_array[i][0] + ROI_dim_array[i][2]

                ROI_xy = (df_build['x'] >= left) & (df_build['x'] < right) & (
                    df_build['y'] >= bottom) & (df_build['y'] < top)
                df_build["ROI"][ROI_xy] = ROI
                print(f"ROI {i} is selected and labeled {ROI}!")

                ROI_edge = pd.concat([ROI_edge, pd.Series(
                    [ROI, bottom, top, left, right, rep])], axis=1)

            self._df_pixel_all = pd.concat([self._df_pixel_all, df_build])

        self._df_pixel_all.reset_index(drop=True, inplace=True)
        self._df_pixel_all.columns = self._df_pixel_all.columns.astype(str)
        self.df_pixel_all = self._df_pixel_all.copy()
        ROI_edge = ROI_edge.T.reset_index(drop=True)
        ROI_edge.columns = ["ROI", "bottom",
                            "top", "left", "right", "replicate"]
        self.ROI_info = ROI_edge

    def calibrate_mz(
            self
            ):
        """
        Interactive calibration using polynomial regression via linear model of user-specified degree
        
        Parameters
        ----------
        degree : int, user input
            Degree for linear model used in polynomial regression calibration
        reference_mz : 1darray, user input
            Reference massess to perform calibration on
        calibration_exit : str, user input
            Exits calibration if user specifies `yes`
        """
        if not os.path.exists(f"{self.data_dir}/figures"):
            os.makedirs(f"{self.data_dir}/figures")
        if not os.path.exists(f"{self.data_dir}/figures/calibration"):
            os.makedirs(f"{self.data_dir}/figures/calibration")
        plt.style.use('default')

        exit_regression = False
        while not exit_regression:
            degree = int(
                input("What is the degree of linear model for calibration? Enter an integer. "))
            reference_mz = input("What are your reference m/z's? Separate each reference m/z by one space. ")
            reference_mz = reference_mz.split(" ")
            reference_mz = np.asarray(reference_mz).astype(np.float32)
            resid_index = np.argmin(
                abs((self.mz.to_numpy()[:, np.newaxis] - reference_mz[np.newaxis, :])), axis=0)
            resid = self.mz[resid_index].to_numpy() - reference_mz

            poly = PolynomialFeatures(degree=degree, include_bias=False)
            poly_features_resid = poly.fit_transform(
                self.mz[resid_index].to_numpy().reshape(-1, 1))
            poly_reg_model = LinearRegression()
            poly_reg_model.fit(poly_features_resid, resid)
            R2 = poly_reg_model.score(poly_features_resid, resid)

            poly_features = poly.fit_transform(
                self.mz.to_numpy().reshape(-1, 1))
            resid_predicted = poly_reg_model.predict(poly_features)

            fig, ax = plt.subplots(
                figsize=(self.fig_ratio, self.fig_ratio*0.7))
            ax.scatter(self.mz[resid_index],
                       resid_predicted[resid_index]/self.mz[resid_index]*10**6, color="red")
            resid_index_compl = np.ones(self.mz.shape[0], dtype=bool)
            resid_index_compl[resid_index] = False
            ax.scatter(self.mz[resid_index_compl],
                       np.zeros_like(self.mz[resid_index_compl]), color="blue", alpha=0.25)
            ax.plot(self.mz[resid_index], resid_predicted[resid_index]/self.mz[resid_index]*10**6, color='black')
            ax.set_title(f'Linear model of degree {degree}')
            ax.set_xlabel('Experimental m/z')
            ax.set_ylabel('Residual [ppm]')
            ax.text(0.5, 0.95, f'$R^2 = {R2:.3f}$', fontsize=12, horizontalalignment='center',
                    verticalalignment='top', transform=plt.gca().transAxes)
            plt.show()

            calibration_exit = input("Accept changes to calibration? (yes/no/exit) ")
            if calibration_exit == "yes":
                self.mz -= resid_predicted
                fig.savefig(
                    f"{self.data_dir}/figures/calibration/accepted_calibration_degree{degree}.png")
                exit_regression = True
            elif calibration_exit == "no":
                exit_regression = False
                plt.close(fig)
            else:
                plt.close(fig)
                pass

    def MS1_search(
            self, 
            ppm_threshold : float = 5,
            MS1_search_method : str = "avg_spectrum",
            filter_db : bool = True,
            percent_RAM : float = 5
            ):
        """MS1 accurate mass search using database from user

        Parameters
        ----------
        ppm_threshold : int, optional
            ppm threshold for MS1 accurate mass hits, by default 5
        MS1_search_method : str, optional
            Method for MS1 hits, by default `avg_spectrum`
            Method `avg_spectrum` performs MS1 search against an average spectrum
            Method `multi_spectrum` performs MS1 search against a collection of spectra stored in a csv file
        filter_db : bool, optional
            Filters MS1 database prior to accurate mass search if `filter_db = True`, by default True
        percent_RAM : float, optional
            Percent available RAM to define size of chunking, by deafult 5
        """
        ion_type = input(
            "What are your ion types of interest? Separate each ion type by one space? ")
        self.ion_type = ion_type.split(" ")
        mass_diff = input(
            "What is the corresponding mass difference of your ion type(s)? List ion types in same order as previously aand separate each ion type by one space. ")
        mass_diff = mass_diff.split(" ")
        self.mass_diff = np.asarray(mass_diff).astype(np.float32)
        
        MS1_db_path = input(
            "What is the file path of MS1 database in csv format? ")
        mz_col = int(input(
            "Which column in your MS1 database corresponds to neurtral monoisotopic masses? ")) - 1
        print("Importing MS1 database. . .")
        MS1_db = pd.read_csv(MS1_db_path)
        print("Finished importing MS1 database!")
        print(f"The columns of imported database are {MS1_db.columns}")
        
        filter_db = str2bool(input("Filter database? Recommended for speed. Enter (yes / no) "))
        if filter_db:
            self.col_filter = int(input("Which column [number] in database to perform filtering? ")) - 1
            filter_by = input(
                f"What are the groups of interest for filtering column {self.col_filter+1} in MS1 database? Separate each group by one space? ")
            self.filter_by = filter_by.split(" ")
            MS1_db = MS1_db.loc[MS1_db.iloc[:,self.col_filter].str.contains('|'.join(self.filter_by))]
            MS1_db.reset_index(drop=True, inplace=True)
            
        @jit(nopython=True)
        def get_MS1_hits(mz, MS1_ion_mass, ppm_threshold):
            MS1_hits = np.abs((mz - MS1_ion_mass) / mz * 10**6) < ppm_threshold
            MS1_hit_indices = np.where(MS1_hits)
            ppm = np.abs((mz - MS1_ion_mass) / mz * 10**6)
            return MS1_hit_indices, ppm
        
        all_rows = []
        
        MS1_db.rename(
            columns={MS1_db.columns[mz_col]: 'exact_mass'}, inplace=True)
        MS1_db_array = np.asarray(MS1_db)
        
        if MS1_search_method == "avg_spectrum":
            mz = self.mz.copy()
            mz = np.asarray(mz).reshape([-1,1]).astype(np.float32)
            for mz_diff, adduct in zip(self.mass_diff, ion_type):
                MS1 = MS1_db.copy()
                MS1["ion_mass"] = MS1["exact_mass"] + mz_diff
                MS1["ion_type"] = adduct
                
                MS1_ion_mass = MS1["ion_mass"].to_numpy().astype(np.float32)
                MS1_hit_indices = get_MS1_hits(mz, MS1_ion_mass, ppm_threshold)
              
                for mz_index, ion_index in zip(*MS1_hit_indices):
                    combined_row = np.append([mz_index], MS1_db_array[ion_index])
                    all_rows.append(combined_row)
        elif MS1_search_method == "multi_spectrum":
            mz_list_path = input("Enter the file path of list of m/z's: ")
            mz = pd.read_csv(mz_list_path)
            analyte_index = np.repeat(np.arange(mz.shape[0]), mz.shape[1]).reshape([-1,1]).astype(np.float32)
            mz = mz.to_numpy().reshape([-1,1]).astype(np.float32)
            mz = np.column_stack((analyte_index, mz))
            RAM_available = psutil.virtual_memory()[1]
            num_chunks = int(mz.shape[0] * MS1_db_array.shape[0] *
                             np.dtype(np.float32).itemsize / (RAM_available * percent_RAM/100)) + 1 
            chunk_size_base = mz.shape[0] // num_chunks
            remainder =  mz.shape[0] % num_chunks
            for mz_diff, adduct in zip(self.mass_diff, self.ion_type):
                MS1 = MS1_db.copy()
                MS1["ion_mass"] = MS1["exact_mass"] + mz_diff
                MS1["ion_type"] = adduct
                MS1_db_array = np.asarray(MS1)
                MS1_ion_mass = MS1["ion_mass"].to_numpy().astype(np.float32)
                chunk_start = 0
                for i in range(num_chunks):
                    chunk_size_temp = chunk_size_base
                    if remainder > i:
                        chunk_size_temp += 1
                    print(f'mz_chunk: {i}; chunck_size: {chunk_size_temp}')
                    if i != 0:
                        chunk_start += chunk_size_temp
                    mz_chunk = mz[chunk_start:(chunk_start+chunk_size_temp)]
                    MS1_hit_indices, ppm = get_MS1_hits(mz_chunk[:,1].reshape([-1,1]), MS1_ion_mass, ppm_threshold)
                    for analyte_pointer, ion_index in zip(*MS1_hit_indices):
                        combined_row = np.append([int(mz[analyte_pointer+chunk_start,0]), ppm[analyte_pointer,ion_index]], MS1_db_array[ion_index])
                        all_rows.append(combined_row)
                        
        self.ms1_df = pd.DataFrame(all_rows, columns=['analyte', 'ppm'] + MS1.columns.to_list())
        
        if MS1_search_method == "multi_spectrum":
            self.ms1_df = self.ms1_df.groupby(list(self.ms1_df.columns[[0,2,3,4,5,6,7,11,12]])).agg(
                    frequency=('ppm', 'size'),    
                    ppm_mean=('ppm', 'mean'),
                    ppm_std=('ppm', 'std')
                ).reset_index()
            self.ms1_df['frequency'] *= (np.max(mz[:,0])+1)/mz.shape[0]*100


    def filter_analytes(
            self, 
            method : str = "MS1"
            ):
        """
        Subset peak-picked untargeted data to analytes of interest

        Parameters
        ----------
        method : str, optional
            Type of filtering, by default "MS1"
            Method `MS1" subsets` untargeted data to MS1 hits 
            Method `analyte_class` subsets untargeted data to analyte classes from MS1 hits
        """
        truncate_col = np.where(self.df_pixel_all.columns == "x")[0][0]
        if method == "MS1":
            pass
        elif method == "MS2":
            MS2_path = input("Enter file path of MS2 results: ")
            MS2_results = pd.read_csv(MS2_path)
                        
            if not any(self.ms1_df.columns == "adduct"):
                ion_type = np.unique(self.ms1_df['ion_type'])
                adduct_mapping = input(f"Map {ion_type} to the following. Separate each value by one space: ")
                adduct_mapping = adduct_mapping.split(" ")
                mapping_dict = dict(zip(ion_type, adduct_mapping))
                self.ms1_df['adduct'] = self.ms1_df['ion_type'].replace(mapping_dict).fillna('unknown')
            analyte_col = int(input(f"Enter column number that corresponds to analyte IDs. The columns are {self.ms1_df.columns}: ")) - 1
            
            self.ms1_df = pd.merge(self.ms1_df, MS2_results, left_on=[f'{self.ms1_df.columns[analyte_col]}', 'adduct'],right_on=['bulk_ID','adduct'], how='inner', suffixes=('', '_ms2'))
            self.ms1_df = self.ms1_df.iloc[self.ms1_df[["adduct","ns_ID"]].drop_duplicates().index].reset_index(drop=True)
        elif method == "analyte_class":
            print(f"The columns in MS1 dataframe are {self.ms1_df.columns}")
            class_col = int(input("Which column in your MS1 dataframe corresponds to analyte class? The columns are {self.ms1_df.columns}: ")) - 1
            analyte_class = input(
                "What are your analyte classes of interest? Separate each analyte class by one space: ")
            self.analyte_class = analyte_class.split(" ")
            self.ms1_df.loc[self.ms1_df.iloc[:,class_col].str.contains('|'.join(analyte_class))].reset_index(drop=True, inplace=True)
        elif method == "ion_type":   
            print(f"The columns in MS1 dataframe are {self.ms1_df.columns}")
            ion_col = int(input("Which column in your MS1 dataframe corresponds to ion type? ")) - 1
            ion_type = input(
                "What are your ion types of interest? Separate each ion type by one space: ")
            self.ms1_df.loc[self.ms1_df.iloc[:,ion_col].str.contains('|'.join(ion_type))].reset_index(drop=True, inplace=True)
        else:
            print(
                "Method not recognized. Please choose from options specified in documentation. ")
            return print("No filtering was performed.")
        
        self.df_pixel_all = pd.concat([self.df_pixel_all.iloc[:, np.unique(
            self.ms1_df['analyte'])], self.df_pixel_all.iloc[:, truncate_col:]], axis=1)
        self.mz = self.mz[np.unique(self.ms1_df['analyte'])]
        self.mz.reset_index(drop=True, inplace=True)
        analyte_mapping = {old_analyte: new_analyte for new_analyte, old_analyte in enumerate(np.unique(self.ms1_df['analyte']))}
        self.ms1_df['analyte'] = self.ms1_df['analyte'].squeeze().map(analyte_mapping)
        truncate_col = np.where(self.df_pixel_all.columns == "x")[0][0]
        self.df_pixel_all.columns = np.append(np.arange(truncate_col).astype(
            str), self.df_pixel_all.columns[truncate_col:])


    def normalize_pixel(
            self, 
            method : str = "TIC"
            ):
        """Normalize pixels using specified method

        Parameters
        ----------
        method : str, optional
            Method to compute normalization factor, by default "TIC"
            Method `TIC` normalizes on total ion count over each pixel
            Method `RMS` normalizes on root mean square over each pixel
            Method `reference` normalizes on a reference analyte specified by m/z or index
            Method `max` normalizes on maximum intensity of each pixel
        """
        print(f"Normalizing pixels with {method}.")
        truncate_col = np.where(self.df_pixel_all.columns == 'x')[0][0]
        if method == "TIC":
            self.df_pixel_all.iloc[:, :truncate_col] = self.df_pixel_all.iloc[:, :truncate_col].div(
                np.sum(self.df_pixel_all.iloc[:, :truncate_col], axis=1), axis=0
            )
        elif method == "RMS":
            self.df_pixel_all.iloc[:, :truncate_col] = np.sqrt(
                self.df_pixel_all.iloc[:, :truncate_col]**2 / self.df_pixel_all.shape[1])
        elif method == "reference":
            mz_reference = input("Enter m/z of reference mass: ")
            ref_col = np.argmin(np.abs(self.mz - mz_reference))
            self.df_pixel_all.iloc[:, :truncate_col] = self.df_pixel_all.iloc[:, :truncate_col].div(
                self.df_pixel_all.iloc[:, ref_col], axis=0
            )
        elif method == "max":
            self.df_pixel_all.iloc[:, :truncate_col] = self.df_pixel_all.iloc[:, :truncate_col].div(
                np.max(self.df_pixel_all.iloc[:, :truncate_col], axis=1), axis=0
            )
        else:
            print("Normalization method not found. Default to no normalization")
            pass

    def get_ion_image(
            self, 
            replicate : int = 0, 
            show_ROI : bool = True, 
            show_square : bool = True, 
            color_scheme : str = "inferno", 
            ROI_size_divisor : float = 8,
            quantile : float = 100
            ):
        """
        Render ion image for analytes in self._df_pixel_all (filtered or unfiltered)

        Parameters
        ----------
        replicate : int, optional
            Render image from replicate (dataset) #, by default 0
        show_ROI : bool, optional
            Display ROI label above redenred ion image, by default True
        show_square : bool, optional
            Display a green box around selected ROI in rendered ion image, by default False
        color_scheme : str, optional
            False-color scheme for ion image visualization, by default "inferno". A list of color schemes are available here: https://matplotlib.org/stable/users/explain/colors/colormaps.html
        ROI_size_divisor : float, optional
            Controls size of ROI labels, where a smaller divisor gives a larger ROI label, by default 8
        quantile : float, optional
            Quantile of intensity (possible values in [0, 100]) for ion image visualization, by default 100
        """
        if not os.path.exists(f"{self.data_dir}/figures"):
            os.makedirs(f"{self.data_dir}/figures")
        if not os.path.exists(f"{self.data_dir}/figures/ion_image"):
            os.makedirs(f"{self.data_dir}/figures/ion_image")
            
        resolution = 4*float(input("Enter spatial resolution of imaging [microns]: "))

        plt.style.use('default')

        truncate_col = np.where(self.df_pixel_all.columns == 'x')[0][0] + 2
        self.df_pixel_all.iloc[:, :truncate_col -
                               2] = self.df_pixel_all.iloc[:, :truncate_col-2].fillna(0)
        insitu_df = pd.concat([self.df_pixel_all['replicate'],
                               self.df_pixel_all.iloc[:, :truncate_col]],
                              axis=1)
        insitu_df = insitu_df[insitu_df['replicate'] == replicate]
        insitu_df = insitu_df.drop(columns=['replicate'])

        if self.jit:
            insitu_2darray = insitu_df.to_numpy()
            img_array_1c = np.zeros([insitu_2darray.shape[1]-2, int(max(insitu_2darray[:, -1]))+1,
                                     int(max(insitu_2darray[:, -2]))+1, 1])
            insitu_image = make_image_1c_njit(
                data_2darray=insitu_2darray, img_array_1c=img_array_1c, remap_coord=False)
            del insitu_2darray, img_array_1c
        else:
            insitu_image = make_image_1c(
                data_2darray=insitu_df.to_numpy(), remap_coord=False, max_normalize=False)

        ROI_info = self.ROI_info[self.ROI_info["replicate"] == replicate]
        ROI_info.reset_index(drop=True, inplace=True)
        
        show_ROI=True
        show_square=True
        width_ratios = (ROI_info['right']-ROI_info['left']).to_numpy().astype(np.float32)
        width_ratios /= max(width_ratios)
        width_ratios = width_ratios.tolist()
        plt.rcParams.update({'font.size': np.max(self.df_pixel_all[['x', 'y']]).max()/20})
        ROI_label_size = max(ROI_info['right']-ROI_info['left']) / ROI_size_divisor
        for analyte, im in enumerate(insitu_image[:, :, :, 0]):
            plt.style.use('dark_background')
            fig = plt.figure()
            gs = GridSpec(1, len(ROI_info) + 1, width_ratios= width_ratios + [0.1], figure=fig, wspace=0.25)
            
            q_max = np.percentile(im, quantile)
            norm = Normalize(vmin=im.min(), vmax=q_max)
            im = norm(im)
            im = np.clip(im, 0, 1)
            ROI_max = 0
            for ax_index in range(len(ROI_info)):
                ax = fig.add_subplot(gs[0, ax_index])
                ROI = pd.DataFrame(ROI_info.loc[ax_index]).T
                start_col = np.where(ROI.columns == "bottom")[0][0]
                truncate_col = np.where(ROI_info.columns == "right")[0][0]
                squares = ROI.iloc[:, start_col:truncate_col+1].to_numpy()
        
                im_ROI = im[squares[0][0].astype(int):squares[0][1].astype(int)+1, squares[0][2].astype(int):squares[0][3].astype(int)+1]
                if np.max(im_ROI) > ROI_max:
                    ROI_max = np.max(im_ROI)
                ax.imshow(im_ROI, cmap=color_scheme, vmin=0, vmax=1)
                plt.sca(ax)
                for i, square in enumerate(squares):
                    bottom, top, left, right = square
                    top -= bottom
                    bottom -= bottom
                    right -= left
                    left -= left
                    x_coords = [left, left, right, right, left]
                    y_coords = [bottom, top, top, bottom, bottom]
                    if show_square:
                        plt.plot(x_coords, y_coords, 'g-', linewidth=3)
                    if show_ROI:
                        plt.text(right/2, bottom-1, ROI.iloc[0,0],
                                 horizontalalignment='center', size=ROI_label_size, color='white')
        
                ax.set_xticks([])
                ax.set_yticks([])
                ax.axis('off')
            
            cax = fig.add_subplot(gs[0, -1])
            fig.suptitle(f"m/z {self.mz[analyte]:.3f}", fontsize=ROI_label_size, fontweight='bold')
            scalar_mappable = ScalarMappable(norm=norm, cmap=color_scheme)
            scalar_mappable.set_clim(0, quantile/100)
            colorbar = plt.colorbar(scalar_mappable, cax=cax, orientation='vertical')
            colorbar.set_ticks([0, quantile/100])
            colorbar.set_ticklabels([0, quantile])
            cax.set_position([cax.get_position().x0, cax.get_position().y0 + cax.get_position().height * 0.25, cax.get_position().width, cax.get_position().height * 0.5])

            cax_pos = cax.get_position()
            line_y = cax_pos.y1 + 0.05
            line_x_start = cax_pos.x0
            line_x_end = cax_pos.x1
            
            line = Line2D([line_x_start, line_x_end], [line_y, line_y], color='white', transform=fig.transFigure, clip_on=False, linewidth=ROI_label_size/10)
            fig.add_artist(line)
            fig.text((line_x_start + line_x_end) / 2, line_y + 0.02, f'{resolution:.0f} $\mu$m', ha='center', va='center', color='white', fontsize=ROI_label_size/2)
            plt.savefig(f"{self.data_dir}/figures/ion_image/mz_{self.mz[analyte]:.3f}_replicate{replicate}.png", bbox_inches='tight')
            plt.show()

    def make_FC_plot(
            self, 
            pthreshold : float = 0.05, 
            FCthreshold : float = 1.5,
            legend_label : str = "condition",
            feature_label : str = "mz",
            hm_label : str = "analyte_class",
            jitter_amount : float = 2,
            jitter_factor : float = 100,
            font_size : float = 15,
            get_hm : bool = True,
            hm_width_factor : float = 10,
            hm_height_factor : float = 30,
            hm_fontsize : float = 10,
            hm_wspace : float = 1.5
            ):
        """
        Generate volcano plots of permuted ROI pairs, showing fold-change statistics and p-values 

        Parameters
        ----------
        pthreshold : float, optional
            P-value threshold for statistical significance in volcano plot, by default 0.05
        FCthreshold : float, optional
            Absolute fold change threshold for significant dysregulation in volcano plot, by default 1.5
        legend_label : str, optional
            Labeling scheme for legend of volcano plot, by default `condition`
            Method `condition` colors data points by expression condition
            Method `analyte_class` colors significant data points by class of analyte from MS1 search. Prequisite: DataAnalysis.MS1_search()
        feature_label : str, optional
            Labeling scehem for data points in volcano plot, by default `mz`
            Method `mz` labels significant data points by their corresponding m/z values
            Method `analyte` labels significant data points by their corresponding analylte IDs.Prequisite: DataAnalysis.MS1_search()
        jitter_amount : float, optional
            Controls placement of feature label in volcano plot, by default 2
        jitter_factor : float, optional
            Controls the repulsiveness of neighboring feature labels, by deafult 100
        font_size : float, optional
            Font size of feature labels in volcano plot
        get_hm : bool, optional
            Renders a heatmap of feature label by ROI with entries fold change if `get_hm = True`, by deafult True
        hm_width_factor : float, optional
            Controls width of heatmaps
        hm_height_factor : float, optional
            Controls height of heatmap
        hm_fontsize : float, optional
            Controls font size in heatmap
        hm_wspace : float ,optional
            Controls column spacing between heatmaps
        """
        if not os.path.exists(f"{self.data_dir}/figures"):
            os.makedirs(f"{self.data_dir}/figures")
        if not os.path.exists(f"{self.data_dir}/figures/volcano"):
            os.makedirs(f"{self.data_dir}/figures/volcano")
        if not os.path.exists(f"{self.data_dir}/figures/volcano_unlabeled"):
            os.makedirs(f"{self.data_dir}/figures/volcano_unlabeled")

        plt.style.use('default')
        if feature_label == "analyte":
            analyte_col = int(input(f"Enter column number that corresponds to analyte IDs. The columns are {self.ms1_df.columns}: ")) - 1

        # prepare dataframe for p-value and FC computations
        self.df_mean_all = self.df_pixel_all.groupby(
            ['ROI', 'replicate']).mean().reset_index()
        self.df_mean_all.columns = self.df_mean_all.columns.astype(str)
        self.df_mean_all = self.df_mean_all[self.df_mean_all['ROI']
                                            != 'placeholder']
        df_mean_all_long = pd.melt(self.df_mean_all, id_vars=[
                                   'ROI', 'replicate', 'x', 'y'], var_name='analyte', value_name='intensity')
        df_mean_all_long['analyte'] = df_mean_all_long['analyte'].astype(int)
        df_FC = df_mean_all_long.groupby(
            ['ROI', 'analyte']).mean().reset_index()
        df_hm = df_FC.copy()

        print("Starting to generate volcano plots.")
        for combo in list(permutations(df_FC['ROI'].unique(), 2)):
            df_FC_pair = df_FC[(df_FC['ROI'] == combo[0]) |
                               (df_FC['ROI'] == combo[1])]

            reference_intensity = df_FC_pair[df_FC_pair['ROI'] == combo[1]].groupby('analyte')[
                'intensity'].mean()
            df_FC_pair = df_FC_pair.merge(
                reference_intensity, on='analyte', suffixes=('', '_ref'))
            df_FC_pair = df_FC_pair[(
                df_FC_pair[['intensity_ref']] != 0).all(axis=1)]
            df_FC_pair['intensity'] /= df_FC_pair['intensity_ref']

            df_FC_pair['intensity'] = np.log2(df_FC_pair['intensity'])

            df_FC_pair['p'] = 1
            # compute p-value for each analyte
            for analyte_sele in df_FC_pair['analyte'].unique():
                df_analyte = df_mean_all_long[df_mean_all_long['analyte']
                                              == analyte_sele]
                lm_df = ols('intensity ~ C(ROI)',
                            data=df_analyte).fit()
                anova_df = sm.stats.anova_lm(lm_df, typ=1)

                df_FC_pair.loc[df_FC_pair['analyte'] ==
                               analyte_sele, 'p'] = anova_df['PR(>F)'][0]

            df_FC_pair['p'] = -np.log10(df_FC_pair['p'])
            df_FC_pair['legend'] = "placeholder" 
            df_vp = df_FC_pair[df_FC_pair['ROI'] == combo[0]].set_index('analyte')
            
            if legend_label == "condition":
                palette = {
                    "placeholder": "grey",   
                    "downregulated": "blue",  
                    "upregulated": "red"      
                }
                df_vp['legend'].loc[(df_vp['p'] > -np.log10(pthreshold)) & (
                    df_vp['intensity'] < -np.log2(FCthreshold))] = "downregulated"
                df_vp['legend'].loc[(df_vp['p'] > -np.log10(pthreshold)) & (
                    df_vp['intensity'] > np.log2(FCthreshold))] = "upregulated"
            elif legend_label == "analyte_class":
                color_keys = list(mcolors.TABLEAU_COLORS.keys())[:len(self.filter_by)]
                palette = dict(zip(self.filter_by, color_keys))
                palette = {"placeholder":"grey"} | palette
                vp_features = self.ms1_df.iloc[:,[0,self.col_filter+1]].drop_duplicates().to_numpy()
                df_vp = df_vp.iloc[vp_features[:,0].astype(int)]
                vp_indices = (df_vp['p'] > -np.log10(pthreshold)) & (abs(df_vp['intensity']) > np.log2(FCthreshold))      
                df_vp.loc[vp_indices,"legend"] = vp_features[vp_indices,1]
                
            plt.rcParams.update({'font.size': 200/self.fig_ratio*1.5})
            fig = plt.figure(figsize=(self.fig_ratio*1.5, self.fig_ratio*1.5))
            ax = fig.add_subplot()
            scatter_plt = sns.scatterplot(data=df_vp,
                x="intensity", y="p", hue="legend",
                palette=palette, s=80, ax=ax)
                
            ax.set_title(f'{combo[0]} / {combo[1]}')
            ax.set_xlabel('Log2 FC')
            ax.set_ylabel('-Log10 p_value')
            plt.axvline(x=-np.log2(FCthreshold), color='black',
                        linewidth=1.5, linestyle='--')
            plt.axvline(x=np.log2(FCthreshold), color='black',
                        linewidth=1.5, linestyle='--')
            plt.axhline(y=-np.log10(pthreshold), color='black',
                        linewidth=1.5, linestyle='--')

            if feature_label == "mz":
                vp_indices = (df_vp['p'] > -np.log10(pthreshold)) & (abs(df_vp['intensity']) > np.log2(FCthreshold))      
                try:
                    repel_labels(ax, df_vp['intensity'][vp_indices],  df_vp['p'][vp_indices], 
                                  np.round(self.mz.to_numpy(),1)[vp_indices], k=jitter_amount, jitter_factor=jitter_factor, font_size=font_size)
                except NameError:
                    pass
            elif feature_label == "analyte":    
                df_vp_mapping = df_vp.iloc[self.ms1_df['analyte'].to_numpy().astype(int)]
                vp_indices = (df_vp_mapping['p'] > -np.log10(pthreshold)) & (abs(df_vp_mapping['intensity']) > np.log2(FCthreshold))      
                try:
                    repel_labels(ax, df_vp_mapping['intensity'][vp_indices],  df_vp_mapping['p'][vp_indices], 
                                  self.ms1_df.iloc[:,analyte_col][vp_indices.reset_index(drop=True)], k=jitter_amount, 
                                  jitter_factor=jitter_factor, font_size=font_size)

                except NameError:
                    pass
            handles, labels = scatter_plt.get_legend_handles_labels()
            filtered_handles = [h for h, l in zip(handles, labels) if l != 'placeholder']
            filtered_labels = [l for l in labels if l != 'placeholder']
            ax.legend(filtered_handles, filtered_labels)

            plt.tight_layout()
            plt.savefig(
                f"{self.data_dir}/figures/volcano/{combo[0]}_{combo[1]}", dpi=300)
            plt.show()

            # vocalno unlabeled
            plt.rcParams.update({'font.size': 200/self.fig_ratio*1.5})
            fig = plt.figure(figsize=(self.fig_ratio*1.5, self.fig_ratio*1.5))
            ax = fig.add_subplot()
            scatter_plt = sns.scatterplot(data=df_vp,
                x="intensity", y="p", hue="legend",
                palette=palette, s=80, ax=ax)
                
            ax.set_title(f'{combo[0]} / {combo[1]}')
            ax.set_xlabel('Log2 FC')
            ax.set_ylabel('-Log10 p_value')
            plt.axvline(x=-np.log2(FCthreshold), color='black',
                        linewidth=1.5, linestyle='--')
            plt.axvline(x=np.log2(FCthreshold), color='black',
                        linewidth=1.5, linestyle='--')
            plt.axhline(y=-np.log10(pthreshold), color='black',
                        linewidth=1.5, linestyle='--')
            handles, labels = scatter_plt.get_legend_handles_labels()
            filtered_handles = [h for h, l in zip(handles, labels) if l != 'placeholder']
            filtered_labels = [l for l in labels if l != 'placeholder']
            ax.legend(filtered_handles, filtered_labels)
            plt.tight_layout()
            plt.savefig(
                f"{self.data_dir}/figures/volcano_unlabeled/{combo[0]}_{combo[1]}", dpi=300)
            plt.show()

            print(f"Finished generating volcano plots. \n\n Volcano plots were stroed in folder {self.data_dir}/figures.")

        if get_hm:
            try:
                df_hm = df_hm.pivot(index="analyte", columns="ROI", values="intensity")
                df_hm = df_hm.iloc[self.ms1_df['analyte'].to_numpy().astype(int)]
                df_hm.set_index(self.ms1_df['ns_ID'], inplace=True)
                df_hm = np.log2(df_hm.div(df_hm.mean(axis=1), axis=0))

                try:
                    self.col_filter
                    self.filter_by
                except AttributeError:
                    self.col_filter = int(input("Which column [number] in database to perform filtering? The columns are {self.ms1_df.columns}: ")) - 1
                    filter_by = input(
                        f"What are the groups of interest for filtering column {self.col_filter+1} in MS1 database? Separate each group by one space? ")
                    self.filter_by = filter_by.split(" ")
                
                fig = plt.figure(figsize=(hm_height_factor*df_hm.shape[1]/df_hm.shape[0]*hm_width_factor*len(self.filter_by), hm_height_factor))
                plt.rcParams.update({'font.size': hm_fontsize})
                gs = GridSpec(1, len(self.filter_by) + 1, width_ratios= [1]*len(self.filter_by) + [0.1], figure=fig, wspace=hm_wspace)
                for k, analyte_class in enumerate(self.filter_by):
                    ax = fig.add_subplot(gs[0, k])
                    
                    df_hm_subset = df_hm[(self.ms1_df.iloc[:,self.col_filter+1] == analyte_class).to_numpy()]
                    hm_plt = ax.imshow(df_hm_subset.to_numpy(), cmap='coolwarm', aspect='auto')
                    hm_plt.set_clim(-3,3)
    
                    for i in range(df_hm_subset.shape[0]):
                        for j in range(df_hm_subset.shape[1]):
                            ax.text(j, i, f'{df_hm_subset.iloc[i, j]:.1f}',
                                    ha='center', va='center', color='black')
                    
                    ax.set_xlabel('')
                    ax.set_ylabel('')
                    ax.set_yticks(np.arange(df_hm_subset.shape[0]))
                    ax.set_yticklabels(df_hm_subset.index)
                    
                    ax.set_xticks(np.arange(df_hm_subset.shape[1]))
                    ax.set_xticklabels(df_hm_subset.columns, rotation=90)  
                cax = fig.add_subplot(gs[0, -1])
                colorbar = plt.colorbar(hm_plt, cax=cax)
                colorbar.set_ticks(range(-3, 3+1))
                colorbar.ax.tick_params(labelsize=15) 
                cax.set_position([cax.get_position().x0, cax.get_position().y0 + cax.get_position().height * 0.3, cax.get_position().width, cax.get_position().height * 0.45])
                plt.savefig(f"{self.data_dir}/figures/volcano/heatmap", dpi=100, bbox_inches='tight')
                plt.show()
            except NameError:
                print('No heatmap generated. `feature_label` == "analyte" is required for heatmap visualization. ')

    def insitu_clustering(
            self, 
            k : int = 10, 
            perplexity : float = 15, 
            replicate : int = 0, 
            show_ROI : bool = True, 
            show_square : bool = True,
            ROI_linewidth : float = 3,
            ROI_size_divisor : float = 8,
            insitu_tsne : bool = False
            ):
        """
        In situ segmentation via k-means clusterin. Groups pixels by similarity in molecular profiles. Outputs in situ visualization of ROIs colored by cluster labels. In situ segmentation with cluster and ROI annotations are visualized in lower-dimensional t-SNE embedding.

        Parameters
        ----------
        k : int, optional
            Number of k-means clusters. The default is 10.
        perplexity : float, optional
            t-SNE embedding parameter, which influences tightness of embedded neighbors. The default is 15.
        replicate : int, optional
            Dataset # of which ion images are rendered. The default is 0.
        show_ROI : bool, optional
            Display ROI label above redenred ion image if True. The default is True.
        show_square : bool, optional
            Display a green box around selected ROI in rendered ion image if True. The default is False.
        ROI_line_width : float, optional
            Controls width of green box surrounding ROIs
        ROI_size_divisor : float, optional
            Controls size of ROI labels, where a smaller divisor gives a larger ROI label, by default 8
        insitu_tsne : bool, optional
            Renders a RGB in situ representation of 3D t-SNE embedding if `insitu_tsne = True`, by default False 
        """
        resolution = 4*float(input("Enter spatial resolution of imaging [microns]: "))
        
        if not os.path.exists(f"{self.data_dir}/figures"):
            os.makedirs(f"{self.data_dir}/figures")
        if not os.path.exists(f"{self.data_dir}/figures/insitu_cluster"):
            os.makedirs(f"{self.data_dir}/figures/insitu_cluster")

        plt.style.use('default')

        # KMeans
        self.k = k
        self.perplexity = perplexity

        truncate_col = np.where(self.df_pixel_all.columns == 'x')[0][0]
        self.df_pixel_all.iloc[:, :truncate_col] = self.df_pixel_all.iloc[:, :truncate_col].fillna(0)
        df_pixel_all_max = self.df_pixel_all.copy()
        df_pixel_all_max = df_pixel_all_max.loc[df_pixel_all_max["ROI"]!="placeholder"]
        df_pixel_all_max.reset_index(drop=True, inplace=True)
        df_pixel_all_max.iloc[:, :truncate_col] = df_pixel_all_max.iloc[:, :truncate_col].div(
            np.max(df_pixel_all_max.iloc[:, :truncate_col], axis=0), axis=1
        )
        df_pixel_all_max.iloc[:, :truncate_col] = df_pixel_all_max.iloc[:, :truncate_col].div(
            np.max(df_pixel_all_max.iloc[:, :truncate_col], axis=1), axis=0
        )
        df_pixel_all_max = df_pixel_all_max.fillna(0)
        num_iters = 1000

        if self.gpu == False:
            print("Spatial segmentation on CPU. Computing time may be long.")
            from sklearn.cluster import KMeans
            from sklearn.manifold import TSNE
            # tSNE
            k_means = KMeans(n_clusters=self.k, init="k-means++", random_state=0,
                             n_init="auto", max_iter=num_iters).fit(df_pixel_all_max.iloc[:, :truncate_col])
            kmeans_labels = k_means.labels_ + 1

            my_random_state = 1
            tsne = TSNE(n_components=2, n_iter=num_iters,
                        perplexity=self.perplexity, random_state=my_random_state)
            tsne_embedding = tsne.fit_transform(
                df_pixel_all_max.iloc[:, :truncate_col])

        elif self.gpu == True:
            try:
                print("Spatial segmentation on GPU.")
                from cuml.manifold import TSNE
                from cuml.cluster import KMeans
                from cuml import using_output_type

                with using_output_type('numpy'):
                    k_means = KMeans(n_clusters=self.k, max_iter=num_iters).fit(
                        df_pixel_all_max.iloc[:, :truncate_col])
                    kmeans_labels = k_means.labels_ + 1

                    my_random_state = 1
                    tsne = TSNE(n_components=2, n_iter=num_iters,
                                perplexity=self.perplexity, random_state=my_random_state,
                                n_neighbors=150)
                    tsne_embedding = tsne.fit_transform(
                        df_pixel_all_max.iloc[:, :truncate_col])

            except:
                print("GPU error. Defaulting to CPU.")
                print("Spatial segmentation on CPU. Computing time may be long.")
                from sklearn.cluster import KMeans
                from sklearn.manifold import TSNE
                k_means = KMeans(n_clusters=self.k, init="k-means++", random_state=0,
                                 n_init="auto",max_iter=num_iters).fit(df_pixel_all_max.iloc[:, :truncate_col])
                kmeans_labels = k_means.labels_ + 1

                my_random_state = 1
                tsne = TSNE(n_components=2, n_iter=num_iters,
                            perplexity=self.perplexity, random_state=my_random_state)
                tsne_embedding = tsne.fit_transform(
                    df_pixel_all_max.iloc[:, :truncate_col])

        df_pixel_all_max['insitu_cluster'] = pd.DataFrame(kmeans_labels)

        plt.figure(figsize=(self.fig_ratio*1.5, self.fig_ratio*1.5))
        plt.rcParams.update({'font.size': 200/self.fig_ratio*1.5})
        cmap = plt.cm.get_cmap('rainbow', len(np.unique(kmeans_labels)))
        for i, label in enumerate(np.unique(kmeans_labels)):
            plt.scatter(tsne_embedding[kmeans_labels == label, 0], tsne_embedding[kmeans_labels == label, 1],
                        c=cmap(i), 
                        alpha=0.6, label=f'Cluster {label}')

        plt.title("k-Means Clusters of Global Molecular Profile in t-SNE Embedding")
        plt.xlabel("t-SNE 1")
        plt.ylabel("t-SNE 2")
        legend = plt.legend(title='Cluster')
        for legend_handle in legend.legendHandles:  
            legend_handle.set_alpha(1)
        plt.tight_layout()
        plt.savefig(
            f"{self.data_dir}/figures/insitu_cluster/kmeans_tsne", dpi=100)

        categories = pd.Categorical(df_pixel_all_max['ROI'])
        unique_categories = categories.categories
        cmap = plt.cm.get_cmap('rainbow', len(unique_categories))

        plt.figure(figsize=(self.fig_ratio*1.5, self.fig_ratio*1.5))
        plt.rcParams.update({'font.size': 200/self.fig_ratio*1.5})

        for i, category in enumerate(unique_categories):
            mask = df_pixel_all_max['ROI'] == category
            plt.scatter(tsne_embedding[mask, 0], tsne_embedding[mask, 1],
                        color=cmap(i), alpha=0.6, label=category)

        plt.title("Global Molecular Profile in t-SNE Embedding")
        plt.xlabel("t-SNE 1")
        plt.ylabel("t-SNE 2")

        legend = plt.legend(title='ROI')
        for legend_handle in legend.legendHandles:
            legend_handle.set_alpha(1)
        plt.tight_layout()
        plt.savefig(
            f"{self.data_dir}/figures/insitu_cluster/original_space_tsne", dpi=100)

        insitu_df = pd.concat([df_pixel_all_max['replicate'], pd.DataFrame(kmeans_labels, columns=["kmeans_label"]),
                               df_pixel_all_max[['x', 'y']]], axis=1)
        insitu_df = insitu_df[insitu_df['replicate'] == replicate]
        insitu_df = insitu_df.drop(columns=['replicate'])

        insitu_image = make_image_1c(
            data_2darray=insitu_df.to_numpy(), remap_coord=False, max_normalize=False)
        insitu_image = np.ma.masked_equal(insitu_image, 0)

        ROI_info = self.ROI_info[self.ROI_info["replicate"] == replicate]
        ROI_info.reset_index(drop=True, inplace=True)
        
        show_ROI=True
        show_square=True
        width_ratios = (ROI_info['right']-ROI_info['left']).to_numpy().astype(np.float32)
        width_ratios /= max(width_ratios)
        width_ratios = width_ratios.tolist()
        plt.style.use('dark_background')
        fig = plt.figure()
        plt.rcParams.update({'font.size': np.max(self.df_pixel_all[['x', 'y']]).max()/20})
        ROI_label_size = max(ROI_info['right']-ROI_info['left']) / ROI_size_divisor
        gs = GridSpec(1, len(ROI_info) + 1, width_ratios= width_ratios + [0.1], figure=fig, wspace=0.25)
        for ax_index in range(len(ROI_info)):
            ax = fig.add_subplot(gs[0, ax_index])
            ROI = pd.DataFrame(ROI_info.loc[ax_index]).T
            start_col = np.where(ROI.columns == "bottom")[0][0]
            truncate_col = np.where(ROI_info.columns == "right")[0][0]
            squares = ROI.iloc[:, start_col:truncate_col+1].to_numpy()
    
            im_ROI = insitu_image[0][squares[0][0].astype(int):squares[0][1].astype(int)+1, squares[0][2].astype(int):squares[0][3].astype(int)+1]
            kmeans_insitu = ax.imshow(im_ROI, cmap="rainbow", vmin=1, vmax=np.max(kmeans_labels))
            plt.sca(ax)
            for i, square in enumerate(squares):
                bottom, top, left, right = square
                top -= bottom
                bottom -= bottom
                right -= left
                left -= left
                x_coords = [left, left, right, right, left]
                y_coords = [bottom, top, top, bottom, bottom]
                if show_square:
                    plt.plot(x_coords, y_coords, 'g-', linewidth=3)
                if show_ROI:
                    plt.text(right/2, bottom-1, ROI.iloc[0,0],
                             horizontalalignment='center', size=ROI_label_size, color='white')
            ax.set_xticks([])
            ax.set_yticks([])
            ax.axis('off')  
        plt.title("")
        cax = fig.add_subplot(gs[0, -1])
        colorbar = plt.colorbar(kmeans_insitu, cax=cax)
        min_val, max_val = 1, np.max(kmeans_labels)
        colorbar.set_ticks(range(int(min_val), int(max_val) + 1))
        cax.set_position([cax.get_position().x0, cax.get_position().y0 + cax.get_position().height * 0.25, cax.get_position().width, cax.get_position().height * 0.5])
        cax_pos = cax.get_position()
        line_y = cax_pos.y1 + 0.05
        line_x_start = cax_pos.x0
        line_x_end = cax_pos.x1
        plt.tight_layout()
        line = Line2D([line_x_start, line_x_end], [line_y, line_y], color='white', transform=fig.transFigure, clip_on=False, linewidth=ROI_linewidth)
        fig.add_artist(line)
        fig.text((line_x_start + line_x_end) / 2, line_y + 0.02, f'{resolution:.0f} $\mu$m', ha='center', va='center', color='white', fontsize=ROI_label_size/2)
        
        plt.savefig(
            f"{self.data_dir}/figures/insitu_cluster/kmeans_insitu_image", dpi=200)
        plt.show(fig)
   
    def optimize_insitu_clustering(
            self, 
            k_max : int = 10
            ):
        """
        In situ segmentation via k-means clusterin. Groups pixels by similarity in molecular profiles. Outputs in situ visualization of ROIs colored by cluster labels. In situ segmentation with cluster and ROI annotations are visualized in lower-dimensional t-SNE embedding.

        Parameters
        ----------
        k_max : int, optional
            Maximum number of clusters for cluster validity evaluation
        """
        if not os.path.exists(f"{self.data_dir}/figures"):
            os.makedirs(f"{self.data_dir}/figures")
        if not os.path.exists(f"{self.data_dir}/figures/insitu_cluster"):
            os.makedirs(f"{self.data_dir}/figures/insitu_cluster")
        score_array = np.zeros((k_max-1,4))

        truncate_col = np.where(self.df_pixel_all.columns == 'x')[0][0]
        self.df_pixel_all.iloc[:, :truncate_col] = self.df_pixel_all.iloc[:, :truncate_col].fillna(0)
        df_pixel_all_max = self.df_pixel_all.copy()
        df_pixel_all_max = df_pixel_all_max.loc[df_pixel_all_max["ROI"]!="placeholder"]
        df_pixel_all_max.reset_index(drop=True, inplace=True)
        df_pixel_all_max.iloc[:, :truncate_col] = df_pixel_all_max.iloc[:, :truncate_col].div(
            np.max(df_pixel_all_max.iloc[:, :truncate_col], axis=0), axis=1
        )
        df_pixel_all_max.iloc[:, :truncate_col] = df_pixel_all_max.iloc[:, :truncate_col].div(
            np.max(df_pixel_all_max.iloc[:, :truncate_col], axis=1), axis=0
        )
        df_pixel_all_max = df_pixel_all_max.fillna(0)
        num_iters = 1000
        for k in range(2, k_max+1):
            self.k = k
            if self.gpu == False:
                print("In situ clustering on CPU. Computing time may be long.")
                from sklearn.cluster import KMeans        
                k_means = KMeans(n_clusters=self.k, init="k-means++", random_state=0,
                                 n_init="auto", max_iter=num_iters).fit(df_pixel_all_max.iloc[:, :truncate_col])
                kmeans_labels = k_means.labels_ + 1
    
            elif self.gpu == True:
                try:
                    print("Image clustering on GPU.")
                    from cuml.cluster import KMeans
                    from cuml import using_output_type
                    with using_output_type('numpy'):
    
                        k_means = KMeans(n_clusters=self.k,max_iter=num_iters).fit(
                            df_pixel_all_max.iloc[:, :truncate_col])
                        kmeans_labels = k_means.labels_ + 1
    
                except:
                    print("GPU error. Defaulting to CPU.")
                    print("Image clustering on CPU. Computing time may be long.")
                    from sklearn.cluster import KMeans
                    k_means = KMeans(n_clusters=self.k, init="k-means++", random_state=0,
                                     n_init="auto",max_iter=num_iters).fit(df_pixel_all_max.iloc[:, :truncate_col])
                    kmeans_labels = k_means.labels_ + 1
    
                score_array[k-2,0] = davies_bouldin_score(df_pixel_all_max.iloc[:, :truncate_col], kmeans_labels)
                score_array[k-2,1] = calinski_harabasz_score(df_pixel_all_max.iloc[:, :truncate_col], kmeans_labels)
                score_array[k-2,2] = silhouette_score(df_pixel_all_max.iloc[:, :truncate_col], k_means.fit_predict(df_pixel_all_max.iloc[:, :truncate_col]))
                score_array[k-2,3] = k_means.inertia_
        plt.style.use('default')      
        categories = ["DB", "CH", "Silhouette", "Elbow"]
        plt.rcParams.update({'font.size': 22})
        fig, ax = plt.subplots(figsize=(10, 7))
        colors = ['blue', 'green', 'red', 'purple', "black"]
        normalized_scores = np.divide(score_array, np.max(score_array,axis=0))
        x_values = np.arange(2,k_max+1)
        for i, (category, color) in enumerate(zip(categories, colors)):
            y_values = normalized_scores[:, i]
            
            plt.scatter(x_values, y_values, color=color, label=category)
            plt.plot(x_values, y_values, color=color)

        plt.xlabel('Number of clusters')
        plt.ylabel('Max-Normalized Scores')

        plt.legend(title='Score', fontsize="small")
        plt.savefig(f"{self.data_dir}/figures/insitu_cluster/insitu_clustering_optimization_plot.png", bbox_inches='tight')
        plt.show()

    def image_clustering(
            self, 
            k : int = 10, 
            perplexity : float = 5, 
            replicate : int = 0, 
            show_ROI : bool = True, 
            show_square : bool = True, 
            color_scheme : str = "inferno", 
            insitu_tsne : bool = False,
            insitu_perplexity : float = 15,
            zoom : float = 0.1, 
            quantile : float = 100,
            img_plot_method : str = "plot_img",
            feature_label = "mz",
            jitter_amount : float = 2,
            jitter_factor : float = 100,
            font_size : float = 5,
            ROI_linewidth : float = 3,
            ROI_size_divisor : float = 8
            ):
        """
        Group ion images by spatial co-localization. Outputs in situ and box plot visualizations of mean ion image for each cluster. Clustering analysis and in situ mapping of clusters are summarized in lower-dimensional t -SNE embedding.

        Parameters
        ----------
        k : int, optional
            Number of k-means clusters. The default is 10.
        perplexity : float, optional
            t-SNE embedding parameter, which influences tightness of embedded neighbors. The default is 5.
        replicate : int, optional
            Dataset # of which ion images are rendered. The default is 0.
        show_ROI : bool, optional
            Display ROI label above redenred ion image if True. The default is True.
        show_square : bool, optional
            Display a green box around selected ROI in rendered ion image if True. The default is False.
        color_scheme : str, optional
            False-color scheme for ion image visualization. The default is "inferno". A list of color schemes are available here: https://matplotlib.org/stable/users/explain/colors/colormaps.html
        zoom : float, optional
            Relative size of ion images in t-SNE embedding to the embedding. The default is 0.1.
        quantile : TYPE, optional
            Maximum intensity quantile cutoff for ion image visualization. The default is 100.
        jitter_amount : float, optional
            Controls placement of feature labels in t-SNE embedding of ion images, by default 2
        jitter_factor : float, optional
            Controls the repulsiveness of neighboring feature labels in t-SNE embedding of ion images, by deafult 100
        font_size : float, optional
            Font size of feature labels in volcano plot
        ROI_line_width : float, optional
            Controls width of green box surrounding ROIs
        ROI_size_divisor : float, optional
            Controls size of ROI labels, where a smaller divisor gives a larger ROI label, by default 8
        """
        if not os.path.exists(f"{self.data_dir}/figures"):
            os.makedirs(f"{self.data_dir}/figures")
        if not os.path.exists(f"{self.data_dir}/figures/image_cluster"):
            os.makedirs(f"{self.data_dir}/figures/image_cluster")
        if feature_label == "analyte":
            analyte_col = int(input(f"Enter column number that corresponds to analyte IDs. The columns are {self.ms1_df.columns}: ")) - 1

        plt.style.use('default')
        resolution = 4*float(input("Enter spatial resolution of imaging [microns]: "))

        self.k = k
        self.perplexity = perplexity

        truncate_col = np.where(self.df_pixel_all.columns == 'x')[0][0]
        self.df_pixel_all.iloc[:, :truncate_col] = self.df_pixel_all.iloc[:, :truncate_col].fillna(
            0)
        df_pixel_all_max = self.df_pixel_all.copy()
        df_pixel_all_max = df_pixel_all_max[df_pixel_all_max["ROI"]!="placeholder"]
        df_pixel_all_max.reset_index(drop=True, inplace=True)
        df_pixel_all_max.iloc[:, :truncate_col] = df_pixel_all_max.iloc[:, :truncate_col].div(
            np.max(df_pixel_all_max.iloc[:, :truncate_col], axis=0))
        num_iters = 1000
        if self.gpu == False:
            print("Image clustering on CPU. Computing time may be long.")
            from sklearn.cluster import KMeans
            from sklearn.manifold import TSNE
            k_means = KMeans(n_clusters=self.k, init="k-means++", random_state=0,
                             n_init="auto",max_iter=num_iters).fit(df_pixel_all_max.iloc[:, :truncate_col].T)
            kmeans_labels = k_means.labels_ + 1

            my_random_state = 1
            tsne = TSNE(n_components=2, n_iter=num_iters,
                        perplexity=self.perplexity, random_state=my_random_state)
            tsne_embedding = tsne.fit_transform(
                df_pixel_all_max.iloc[:, :truncate_col].T)
        elif self.gpu == True:

            try:
                print("Image clustering on GPU.")
                from cuml.manifold import TSNE
                from cuml.cluster import KMeans
                from cuml import using_output_type
                with using_output_type('numpy'):

                    k_means = KMeans(n_clusters=self.k,max_iter=num_iters).fit(
                        df_pixel_all_max.iloc[:, :truncate_col].T)
                    kmeans_labels = k_means.labels_ + 1
                
                    my_random_state = 1
                    tsne = TSNE(n_components=2, n_iter=num_iters,
                                perplexity=self.perplexity, random_state=my_random_state,
                                n_neighbors=150)
                    tsne_embedding = tsne.fit_transform(
                        df_pixel_all_max.iloc[:, :truncate_col].T)
            except:
                print("GPU error. Defaulting to CPU.")
                print("Image clustering on CPU. Computing time may be long.")
                from sklearn.cluster import KMeans
                from sklearn.manifold import TSNE
                k_means = KMeans(n_clusters=self.k, init="k-means++", random_state=0,
                                 n_init="auto", max_iter=num_iters).fit(df_pixel_all_max.iloc[:, :truncate_col].T)
                kmeans_labels = k_means.labels_ + 1
                my_random_state = 1
                tsne = TSNE(n_components=2, n_iter=num_iters,
                            perplexity=self.perplexity, random_state=my_random_state)
                tsne_embedding = tsne.fit_transform(
                    df_pixel_all_max.iloc[:, :truncate_col].T)

        image_cluster_label = pd.DataFrame(kmeans_labels)
        image_cluster_label = image_cluster_label.reset_index()
        image_cluster_label.rename(
            columns={'index': 'analyte', 0: 'cluster'}, inplace=True)

        image_df = df_pixel_all_max.groupby(
            ['ROI', 'replicate']).mean().reset_index()
        cluster_mean_df = image_df['ROI']
        image_cluster_all = df_pixel_all_max['ROI']
        for cluster_index in range(1, k+1):  # loop through clusters
            cluster_df = image_df[image_cluster_label[image_cluster_label['cluster']
                                                      == cluster_index]['analyte'].to_numpy().astype(str)].mean(axis=1)
            cluster_df.name = str(cluster_index)
            cluster_mean_df = pd.concat([cluster_mean_df, cluster_df], axis=1)

            image_cluster = df_pixel_all_max[image_cluster_label[image_cluster_label['cluster']
                                                                 == cluster_index]['analyte'].to_numpy().astype(str)].mean(axis=1)
            image_cluster.name = str(cluster_index)
            image_cluster_all = pd.concat(
                [image_cluster_all, image_cluster], axis=1)

        cluster_mean_df = cluster_mean_df[cluster_mean_df['ROI']
                                          != "placeholder"]

        image_cluster_all[['x', 'y']] = df_pixel_all_max[['x', 'y']]
        image_cluster_all = image_cluster_all[df_pixel_all_max['replicate'] == replicate]

        plt.rcParams.update({'font.size': 200/self.fig_ratio*1.5})
        fig = plt.figure(figsize=(self.fig_ratio*1.5, self.fig_ratio*1.5))
        ax = fig.add_subplot()
        cmap = plt.cm.get_cmap('rainbow', len(np.unique(kmeans_labels)))
        for i, label in enumerate(np.unique(kmeans_labels)):
            ax.scatter(tsne_embedding[kmeans_labels == label, 0], tsne_embedding[kmeans_labels == label, 1],
                        c=cmap(i),  # Normalize color within the colormap range
                        alpha=1, label=f'Cluster {label}', s=50)
        if feature_label == "mz":
            repel_labels(ax, tsne_embedding[:,0], tsne_embedding[:,1], 
                          np.round(self.mz.to_numpy(),1), k=jitter_amount, jitter_factor=jitter_factor, font_size=font_size)
        elif feature_label == "analyte":
            ms1_features = self.ms1_df.iloc[:,[0,analyte_col]].drop_duplicates().to_numpy()
            tsne_mapping = tsne_embedding[ms1_features[:,0].astype(int),]
            repel_labels(ax, tsne_mapping[:,0], tsne_mapping[:,1], 
                          ms1_features[:,1], k=jitter_amount,
                          jitter_factor=jitter_factor, font_size=font_size)
        else:
            print("Method for `feature_label` not found. Defaults to no labeling.")
            pass
            
        ax.set_title("k-Means Clusters of Global Molecular Profile in t-SNE Embedding")
        ax.set_xlabel("t-SNE 1")
        ax.set_ylabel("t-SNE 2")
        legend = plt.legend(title='Cluster')
        for legend_handle in legend.legendHandles: 
            legend_handle.set_alpha(1)
        plt.tight_layout()
        plt.savefig(
            f"{self.data_dir}/figures/image_cluster/image_kmeans_tsne", dpi=100)
        plt.show()

        truncate_col = np.where(df_pixel_all_max.columns == 'x')[0][0] + 2
        insitu_df = pd.concat([df_pixel_all_max['replicate'],
                               df_pixel_all_max.iloc[:, :truncate_col]],
                              axis=1)
        insitu_df = insitu_df[insitu_df['replicate'] == replicate]
        insitu_df = insitu_df.drop(columns=['replicate'])

        if self.jit:
            insitu_2darray = insitu_df.to_numpy()
            img_array_1c = np.zeros([insitu_2darray.shape[1]-2, int(max(insitu_2darray[:, -1]))+1,
                                     int(max(insitu_2darray[:, -2]))+1, 1])
            insitu_image = make_image_1c_njit(
                data_2darray=insitu_2darray, img_array_1c=img_array_1c, remap_coord=False)
            del insitu_2darray, img_array_1c
        else:
            insitu_image = make_image_1c(
                data_2darray=insitu_df.to_numpy(), remap_coord=False, max_normalize=False)

        if img_plot_method == "plot_img":
            plt.figure(figsize=(self.fig_ratio*1.5, self.fig_ratio*1.5))
            plt.rcParams.update({'font.size': 200/self.fig_ratio*1.5})
            plt.scatter(*tsne_embedding.T)
            for im, xy in zip(insitu_image[:, :, :, 0], tsne_embedding):
                q_max = np.percentile(im, quantile)
                norm = Normalize(vmin=im.min(), vmax=q_max)
                im = norm(im)
                im = np.clip(im, 0, 1)
                plot_image_at_point(im, xy, zoom=zoom, color_scheme="inferno")
                plt.title("Images in t-SNE Embedding")
                plt.xlabel("t-SNE 1")
                plt.ylabel("t-SNE 2")
            plt.tight_layout()
            plt.savefig(f"{self.data_dir}/figures/image_cluster/image_insitu_tsne")
        elif img_plot_method == "plot_ROI":
            img_list = []
            ROI_max = 0
            ROI_info = self.ROI_info[self.ROI_info["replicate"] == replicate]
            ROI_info.reset_index(drop=True, inplace=True)
            show_ROI=True
            show_square=True
            color_scheme="inferno"
            width_ratios = (ROI_info['right']-ROI_info['left']).to_numpy().astype(np.float32)
            width_ratios /= max(width_ratios)
            width_ratios = width_ratios.tolist()
            plt.rcParams.update({'font.size': np.max(df_pixel_all_max[['x', 'y']]).max()/20})
            ROI_label_size = max(ROI_info['right']-ROI_info['left'])  / ROI_size_divisor
            truncate_col = np.where(ROI_info.columns == "right")[0][0]
            
            q_max = np.percentile(insitu_image[:, :, :, 0], quantile)
            norm = Normalize(vmin=insitu_image[:, :, :, 0].min(), vmax=q_max)
            insitu_image[:, :, :, 0] = norm(insitu_image[:, :, :, 0])
            insitu_image[:, :, :, 0] = np.clip(insitu_image[:, :, :, 0], 0, 1)
            for analyte, im in enumerate(insitu_image[:, :, :, 0]):
                plt.style.use('dark_background')
                fig = plt.figure()
                gs = GridSpec(1, len(ROI_info), width_ratios= width_ratios, figure=fig, wspace=0.25)

                for ax_index in range(len(ROI_info)):
                    ax = fig.add_subplot(gs[0, ax_index])
                    ROI = pd.DataFrame(ROI_info.loc[ax_index]).T
                    start_col = np.where(ROI.columns == "bottom")[0][0]
                    squares = ROI.iloc[:, start_col:truncate_col+1].to_numpy()
                
                    im_ROI = im[squares[0][0].astype(int):squares[0][1].astype(int)+1, squares[0][2].astype(int):squares[0][3].astype(int)+1]
                    if np.max(im_ROI) > ROI_max:
                        ROI_max = np.max(im_ROI)
                    ax.imshow(im_ROI, cmap=color_scheme)
                    plt.sca(ax)
                    for i, square in enumerate(squares):
                        bottom, top, left, right = square
                        top -= bottom
                        bottom -= bottom
                        right -= left
                        left -= left
                        x_coords = [left, left, right, right, left]
                        y_coords = [bottom, top, top, bottom, bottom]
                        if show_square:
                            plt.plot(x_coords, y_coords, 'g-', linewidth=3)
                        if show_ROI:
                            plt.text(right/2, bottom-1, ROI.iloc[0,0],
                                     horizontalalignment='center', size=ROI_label_size, color='white')
                    ax.set_xticks([])
                    ax.set_yticks([])
                    ax.axis('off')
                    
                fig.canvas.draw()
                plt.close()
                img_array = np.frombuffer(fig.canvas.buffer_rgba(), dtype=np.uint8)
                img_array = img_array.reshape(fig.canvas.get_width_height()[::-1] + (4,))
                img_list.append(img_array)
            
            plt.style.use('default')
            plt.figure(figsize=(self.fig_ratio*1.5, self.fig_ratio*1.5))
            plt.rcParams.update({'font.size': 200/self.fig_ratio*1.5})
            plt.scatter(*tsne_embedding.T)
            for im, xy in zip(img_list, tsne_embedding):
                plot_image_at_point(im, xy, zoom=zoom, color_scheme="inferno")
                plt.title("Images in t-SNE Embedding")
                plt.xlabel("t-SNE 1")
                plt.ylabel("t-SNE 2")
            plt.savefig(f"{self.data_dir}/figures/image_cluster/image_insitu_tsne", bbox_inches='tight')
            plt.show()
                
        plt.style.use('default')
        image_cluster_df_long = pd.melt(cluster_mean_df, id_vars=[
                                        'ROI'], var_name='cluster', value_name='intensity')
        image_cluster_df_long['intensity'] = image_cluster_df_long['intensity'].div(
            image_cluster_df_long.groupby(['cluster']).transform('max')['intensity'])

        ROI = image_cluster_df_long["ROI"]
        for cluster in image_cluster_df_long['cluster'].unique():
            image_cluster_df_cluster = image_cluster_df_long[image_cluster_df_long['cluster'] == cluster]
            pvalue = [
                sm.stats.anova_lm(
                    ols('intensity ~ C(ROI)', data=image_cluster_df_cluster.loc[
                        (image_cluster_df_cluster['ROI'] == combo[0]) |
                        (image_cluster_df_cluster['ROI'] == combo[1]),
                        ['ROI', 'intensity']]
                        ).fit(),
                    typ=1
                )['PR(>F)'][0]

                for combo in list(combinations(ROI.unique(), 2))
            ]
            model_pairs = [
                combo
                for combo in list(combinations(ROI.unique(), 2))
            ]

            fig = plt.figure(figsize=(
                (10+math.comb(self.ROI_num, 2)/10)/2, (7+math.comb(self.ROI_num, 2)/3)/2))
            plt.rcParams.update(
                {'font.size': (10+math.comb(self.ROI_num, 2)/10)})
            ax = fig.add_subplot()
            sns.boxplot(x="ROI", y="intensity",
                        data=image_cluster_df_cluster, color='white')
            sns.stripplot(x="ROI", y="intensity", data=image_cluster_df_cluster,
                          color='red', size=4, jitter=True)

            annotator = Annotator(ax, model_pairs, x="ROI",
                                  y="intensity", data=image_cluster_df_cluster)
            formatted_pvalues = [f'{significance(p)}' for p in pvalue]
            annotator.set_custom_annotations(formatted_pvalues)
            annotator.annotate()

            plt.title(f'Mean image of cluster {cluster}')
            plt.xlabel('ROI')
            plt.ylabel('Mean Intensity')
            plt.tight_layout()
            plt.savefig(
                f"{self.data_dir}/figures/image_cluster/boxplot_cluster{cluster}.jpg", dpi=200)

        image_cluster_all = image_cluster_all.drop(columns=["ROI"])
        if self.jit:
            insitu_2darray = image_cluster_all.to_numpy()
            img_array_1c = np.zeros([insitu_2darray.shape[1]-2, int(max(insitu_2darray[:, -1]))+1,
                                     int(max(insitu_2darray[:, -2]))+1, 1])
            insitu_image = make_image_1c_njit(
                data_2darray=insitu_2darray, img_array_1c=img_array_1c, remap_coord=False)
            del insitu_2darray, img_array_1c
        else:
            insitu_image = make_image_1c(data_2darray=image_cluster_all.to_numpy(),
                                         remap_coord=False, max_normalize=False)

        ROI_info = self.ROI_info[self.ROI_info["replicate"] == replicate]
        ROI_info.reset_index(drop=True, inplace=True)
        
        if img_plot_method == "plot_img":
            for analyte, im in enumerate(insitu_image[:, :, :, 0]):
                q_max = np.percentile(im, quantile)
                norm = Normalize(vmin=im.min(), vmax=q_max)
                im = norm(im)
                im = np.clip(im, 0, 1)
                
                plt.style.use('dark_background')
                fig = plt.figure(figsize=np.max(insitu_df[['x', 'y']], axis=0)/10)
                plt.rcParams.update({'font.size': np.max(
                    df_pixel_all_max[['x', 'y']]).max()/10})
                gs = GridSpec(1, 2, width_ratios=[np.max(df_pixel_all_max[['x', 'y']]).max(), np.max(
                    # image and legend grids
                    df_pixel_all_max[['x', 'y']]).max()/25], wspace=0.05)
                ax1 = fig.add_subplot(gs[0])
                im_plot = ax1.imshow(im, cmap=color_scheme, vmin=0, vmax=1)
                draw_ROI(ROI_info, show_ROI=show_ROI,show_square=show_square, 
                         linewidth=ROI_linewidth, ROI_size_divisor=ROI_size_divisor)
                plt.xticks([])
                plt.yticks([])
                plt.title(f"Mean image of cluster {analyte+1}")
                ax2 = fig.add_subplot(gs[1])
                colorbar = plt.colorbar(im_plot, cax=ax2)
                colorbar.set_ticks([0, 1])
                colorbar.set_ticklabels([0, quantile])
                plt.tight_layout()
                plt.savefig(
                    f"{self.data_dir}/figures/image_cluster/mean_image_cluster{analyte+1}.png")
                plt.show(fig)
            del image_cluster_all
        elif img_plot_method == "plot_ROI":
            show_ROI=True
            show_square=True
            color_scheme="inferno"
            width_ratios = (ROI_info['right']-ROI_info['left']).to_numpy().astype(np.float32)
            width_ratios /= max(width_ratios)
            width_ratios = width_ratios.tolist()
            plt.rcParams.update({'font.size': np.max(df_pixel_all_max[['x', 'y']]).max()/20})
            ROI_label_size = max(ROI_info['right']-ROI_info['left']) / ROI_size_divisor
            
            for analyte, im in enumerate(insitu_image[:, :, :, 0]):
                q_max = np.percentile(im, quantile)
                norm = Normalize(vmin=im.min(), vmax=q_max)
                im = norm(im)
                im = np.clip(im, 0, 1)
                
                plt.style.use('dark_background')
                fig = plt.figure()
                gs = GridSpec(1, len(ROI_info) + 1, width_ratios= width_ratios + [0.1], figure=fig, wspace=0.25)
                
                for ax_index in range(len(ROI_info)):
                    ax = fig.add_subplot(gs[0, ax_index])
                    ROI = pd.DataFrame(ROI_info.loc[ax_index]).T
                    start_col = np.where(ROI.columns == "bottom")[0][0]
                    truncate_col = np.where(ROI_info.columns == "right")[0][0]
                    squares = ROI.iloc[:, start_col:truncate_col+1].to_numpy()
            
                    im_ROI = im[squares[0][0].astype(int):squares[0][1].astype(int)+1, squares[0][2].astype(int):squares[0][3].astype(int)+1]
                    ax.imshow(im_ROI, cmap=color_scheme, vmin=0, vmax=1)
                    plt.sca(ax)
                    for i, square in enumerate(squares):
                        bottom, top, left, right = square
                        top -= bottom
                        bottom -= bottom
                        right -= left
                        left -= left
                        x_coords = [left, left, right, right, left]
                        y_coords = [bottom, top, top, bottom, bottom]
                        if show_square:
                            plt.plot(x_coords, y_coords, 'g-', linewidth=3)
                        if show_ROI:
                            plt.text(right/2, bottom-1, ROI.iloc[0,0],
                                     horizontalalignment='center', size=ROI_label_size, color='white')
            
                    ax.set_xticks([])
                    ax.set_yticks([])
                    ax.axis('off')
                
                cax = fig.add_subplot(gs[0, -1])
                fig.suptitle(f"Mean image of cluster {analyte+1}", fontsize=ROI_label_size, fontweight='bold')
                scalar_mappable = ScalarMappable(norm=norm, cmap=color_scheme)
                scalar_mappable.set_clim(0, 1)
                colorbar = plt.colorbar(scalar_mappable, cax=cax, orientation='vertical')
                colorbar.set_ticks([0, 1])
                colorbar.set_ticklabels([0, quantile])
                cax.set_position([cax.get_position().x0, cax.get_position().y0 + cax.get_position().height * 0.25, cax.get_position().width, cax.get_position().height * 0.5])
                cax_pos = cax.get_position()
                line_y = cax_pos.y1 + 0.05
                line_x_start = cax_pos.x0
                line_x_end = cax_pos.x1
                
                line = Line2D([line_x_start, line_x_end], [line_y, line_y], color='white', transform=fig.transFigure, clip_on=False, linewidth=ROI_linewidth)
                fig.add_artist(line)
                fig.text((line_x_start + line_x_end) / 2, line_y + 0.02, f'{resolution:.0f} $\mu$m', ha='center', va='center', color='white', fontsize=ROI_label_size/2)
                plt.savefig(f"{self.data_dir}/figures/image_cluster/mean_image_cluster{analyte+1}.png", bbox_inches='tight')
                plt.show()
                
        if insitu_tsne:
            print("3D t-SNE not supported on GPU. CPU computing time may be long.")
            truncate_col = np.where(self.df_pixel_all.columns == 'x')[0][0]
            self.df_pixel_all.iloc[:, :truncate_col] = self.df_pixel_all.iloc[:, :truncate_col].fillna(0)
            df_pixel_all_max = self.df_pixel_all.copy()
            df_pixel_all_max = df_pixel_all_max.loc[df_pixel_all_max["ROI"]!="placeholder"]
            df_pixel_all_max.reset_index(drop=True, inplace=True)
            df_pixel_all_max.iloc[:, :truncate_col] = df_pixel_all_max.iloc[:, :truncate_col].div(
                np.max(df_pixel_all_max.iloc[:, :truncate_col], axis=0), axis=1
            )
            df_pixel_all_max.iloc[:, :truncate_col] = df_pixel_all_max.iloc[:, :truncate_col].div(
                np.max(df_pixel_all_max.iloc[:, :truncate_col], axis=1), axis=0
            )
            df_pixel_all_max=df_pixel_all_max.fillna(0)

            from sklearn.manifold import TSNE
            num_iters = 1000
            my_random_state = 1
            for cluster_index in range(1, k+1):
                truncate_col = np.where(df_pixel_all_max.columns == 'x')[0][0]
                try:
                    tsne_3d = TSNE(n_components=3, n_iter=num_iters,
                                perplexity=insitu_perplexity, random_state=my_random_state)
                    tsne_embedding_3d = tsne_3d.fit_transform(
                        df_pixel_all_max.iloc[:, :truncate_col].iloc[:,kmeans_labels==cluster_index])
                except ValueError:
                    try:
                        tsne_3d = TSNE(n_components=2, n_iter=num_iters,
                                    perplexity=insitu_perplexity, random_state=my_random_state)
                        tsne_embedding_3d = tsne_3d.fit_transform(
                            df_pixel_all_max.iloc[:, :truncate_col].iloc[:,kmeans_labels==cluster_index])
                        tsne_embedding_3d = np.hstack((tsne_embedding_3d, np.mean(tsne_embedding_3d,axis=1).reshape((-1,1))))
                    except ValueError:
                        tsne_3d = TSNE(n_components=1, n_iter=num_iters,
                                    perplexity=insitu_perplexity, random_state=my_random_state)
                        tsne_embedding_3d = tsne_3d.fit_transform(
                            df_pixel_all_max.iloc[:, :truncate_col].iloc[:,kmeans_labels==cluster_index])
                        tsne_embedding_3d = np.column_stack((tsne_embedding_3d[:,0],tsne_embedding_3d[:,0],tsne_embedding_3d[:,0]))
    
                
                rgb_tsne = pd.concat([df_pixel_all_max['replicate'], pd.DataFrame(tsne_embedding_3d),
                                       df_pixel_all_max[['x', 'y']]], axis=1)
                rgb_tsne = rgb_tsne[rgb_tsne['replicate'] == replicate]
                rgb_tsne = rgb_tsne.drop(columns=['replicate'])
                rgb_tsne = rgb_tsne.to_numpy()
                
                img_array_1c = np.zeros([rgb_tsne.shape[1]-2, int(max(rgb_tsne[:, -1]))+1,
                                         int(max(rgb_tsne[:, -2]))+1, 1])
                insitu_image = make_image_1c_njit(
                    data_2darray=rgb_tsne, img_array_1c=img_array_1c, remap_coord=False)
                insitu_image = np.ma.masked_equal(insitu_image, 0)
                norm = Normalize(vmin=insitu_image.min(), vmax=insitu_image.max())
                insitu_image = norm(insitu_image)
                insitu_image = np.clip(insitu_image, 0 ,1)
                insitu_image = np.transpose(insitu_image[:, :, :, 0], (1, 2, 0))
                width_ratios = (ROI_info['right']-ROI_info['left']).to_numpy().astype(np.float32)
                width_ratios /= max(width_ratios)
                
                width_ratios = width_ratios.tolist()
                plt.style.use('dark_background')
                fig = plt.figure()
                gs = GridSpec(1, len(ROI_info)+1, width_ratios=width_ratios + [0.1], figure=fig, wspace=0.25)
                ROI_info = self.ROI_info[self.ROI_info["replicate"] == replicate]
                ROI_info.reset_index(drop=True, inplace=True)
            
                plt.rcParams.update({'font.size': np.max(df_pixel_all_max[['x', 'y']]).max()/20})
                ROI_label_size = max(ROI_info['right']-ROI_info['left'])  / ROI_size_divisor
                for ax_index in range(len(ROI_info)):
                    ax = fig.add_subplot(gs[0, ax_index])
                    ROI = pd.DataFrame(ROI_info.loc[ax_index]).T
                    start_col = np.where(ROI.columns == "bottom")[0][0]
                    truncate_col = np.where(ROI_info.columns == "right")[0][0]
                    squares = ROI.iloc[:, start_col:truncate_col+1].to_numpy()
            
                    im_ROI = insitu_image[squares[0][0].astype(int):squares[0][1].astype(int)+1, squares[0][2].astype(int):squares[0][3].astype(int)+1]
                    ax.imshow(im_ROI, vmin=0, vmax=1)
                    plt.sca(ax)
                    for i, square in enumerate(squares):
                        bottom, top, left, right = square
                        top -= bottom
                        bottom -= bottom
                        right -= left
                        left -= left
                        x_coords = [left, left, right, right, left]
                        y_coords = [bottom, top, top, bottom, bottom]
                        if show_square:
                            plt.plot(x_coords, y_coords, 'g-', linewidth=3)
                        if show_ROI:
                            plt.text(right/2, bottom-1, ROI.iloc[0,0],
                                     horizontalalignment='center', size=ROI_label_size, color='white')
            
                    ax.set_xticks([])
                    ax.set_yticks([])
                    ax.axis('off')
                
                plt.savefig(f"{self.data_dir}/figures/image_cluster/insitu_3D_tsne_cluster{cluster_index}.png", bbox_inches='tight')
                plt.show()
                
    def optimize_image_clustering(
           self,
           k_max : int = 10
           ):
        """
        Group ion images by spatial co-localization. Outputs in situ and box plot visualizations of mean ion image for each cluster. Clustering analysis and in situ mapping of clusters are summarized in lower-dimensional t -SNE embedding.

        Parameters
        ----------
        k_max : int, optional
            Maximum number of clusters for cluster validity evaluation
        """
        if not os.path.exists(f"{self.data_dir}/figures"):
            os.makedirs(f"{self.data_dir}/figures")
        if not os.path.exists(f"{self.data_dir}/figures/image_cluster"):
            os.makedirs(f"{self.data_dir}/figures/image_cluster")

        score_array = np.zeros((k_max-1,4))

        truncate_col = np.where(self.df_pixel_all.columns == 'x')[0][0]
        self.df_pixel_all.iloc[:, :truncate_col] = self.df_pixel_all.iloc[:, :truncate_col].fillna(
            0)
        df_pixel_all_max = self.df_pixel_all.copy()
        df_pixel_all_max = df_pixel_all_max[df_pixel_all_max["ROI"]!="placeholder"]
        df_pixel_all_max.reset_index(drop=True, inplace=True)
        df_pixel_all_max.iloc[:, :truncate_col] = df_pixel_all_max.iloc[:, :truncate_col].div(
            np.max(df_pixel_all_max.iloc[:, :truncate_col], axis=0))
        
        num_iters=1000
        for k in range(2, k_max+1):
            self.k = k
            if self.gpu == False:
                print("Image clustering on CPU. Computing time may be long.")
                from sklearn.cluster import KMeans
                k_means = KMeans(n_clusters=self.k, init="k-means++", random_state=0,
                                 n_init="auto", max_iter=num_iters).fit(df_pixel_all_max.iloc[:, :truncate_col].T)
                kmeans_labels = k_means.labels_ + 1
    
            elif self.gpu == True:
    
                try:
                    print("Image clustering on GPU.")
                    from cuml.cluster import KMeans
                    from cuml import using_output_type
    
                    with using_output_type('numpy'):
    
                        k_means = KMeans(n_clusters=self.k,max_iter=num_iters).fit(
                            df_pixel_all_max.iloc[:, :truncate_col].T)
                        kmeans_labels = k_means.labels_ + 1

                except:
                    print("GPU error. Defaulting to CPU.")
                    print("Image clustering on CPU. Computing time may be long.")
                    from sklearn.cluster import KMeans
    
                    k_means = KMeans(n_clusters=self.k, init="k-means++", random_state=0,
                                     n_init="auto",max_iter=num_iters).fit(df_pixel_all_max.iloc[:, :truncate_col].T)
                    kmeans_labels = k_means.labels_ + 1

                score_array[k-2,0] = davies_bouldin_score(df_pixel_all_max.iloc[:, :truncate_col].T, kmeans_labels)
                score_array[k-2,1] = calinski_harabasz_score(df_pixel_all_max.iloc[:, :truncate_col].T, kmeans_labels)
                score_array[k-2,2] = silhouette_score(df_pixel_all_max.iloc[:, :truncate_col].T, k_means.fit_predict(df_pixel_all_max.iloc[:, :truncate_col].T))
                score_array[k-2,3] = k_means.inertia_
                 
        plt.style.use('default')      
        categories = ["DB", "CH", "Silhouette", "Elbow"]
        plt.rcParams.update({'font.size': 22})
        fig, ax = plt.subplots(figsize=(10, 7))
        colors = ['blue', 'green', 'red', 'purple', "black"]
        normalized_scores = np.divide(score_array, np.max(score_array,axis=0))
        x_values = np.arange(2,k_max+1)
        for i, (category, color) in enumerate(zip(categories, colors)):
            y_values = normalized_scores[:, i]
            
            plt.scatter(x_values, y_values, color=color, label=category)
            plt.plot(x_values, y_values, color=color)

        plt.xlabel('Number of clusters')
        plt.ylabel('Max-Normalized Scores')

        plt.legend(title='Score', fontsize="small")
        plt.savefig(f"{self.data_dir}/figures/image_cluster/image_clustering_optimization_plot.png", bbox_inches='tight')
        plt.show()

    def make_boxplot(
            self
            ):
        """
        Box plot comparison of mean ion intensity across ROIs with pairwise statistical comparison. Statistical significance thresholds are represented as `*` if 0.05 > $p$-value $geq$ 0.01, `**` if 0.01 > $p$-value $geq$ 0.001, and `***` if $p$-value $\leq$ 0.001.
        """
        if not os.path.exists(f"{self.data_dir}/figures"):
            os.makedirs(f"{self.data_dir}/figures")
        if not os.path.exists(f"{self.data_dir}/figures/boxplot"):
            os.makedirs(f"{self.data_dir}/figures/boxplot")

        self.df_mean_all = self.df_pixel_all.groupby(
            ['ROI', 'replicate']).mean().reset_index()
        self.df_mean_all.columns = self.df_mean_all.columns.astype(str)
        self.df_mean_all = self.df_mean_all[self.df_mean_all['ROI']
                                            != 'placeholder']
        ROI = self.df_mean_all.iloc[:, 0]
        df_mean_all_long = pd.melt(self.df_mean_all, id_vars=[
                                   'ROI', 'replicate', 'x', 'y'], var_name='analyte', value_name='intensity')
        for analyte in df_mean_all_long['analyte'].unique():
            df_mean_all_analyte = df_mean_all_long[df_mean_all_long['analyte'] == analyte]
            pvalue = [
                sm.stats.anova_lm(
                    ols('intensity ~ C(ROI)', data=df_mean_all_analyte.loc[
                        (df_mean_all_analyte['ROI'] == combo[0]) |
                        (df_mean_all_analyte['ROI'] == combo[1]),
                        ['ROI', 'intensity']]
                        ).fit(),
                    typ=1
                )['PR(>F)'][0]

                for combo in list(combinations(ROI.unique(), 2))
            ]
            model_pairs = [
                combo
                for combo in list(combinations(ROI.unique(), 2))
            ]
            plt.style.use('default')
            fig = plt.figure(
                figsize=(10+math.comb(self.ROI_num, 2)/10, 7+math.comb(self.ROI_num, 2)/3))
            ax = fig.add_subplot()
            plt.rcParams.update({'font.size': (7+math.comb(self.ROI_num, 2))})

            sns.boxplot(x="ROI", y="intensity",
                        data=df_mean_all_analyte, color='white')
            sns.stripplot(x="ROI", y="intensity", data=df_mean_all_analyte,
                          color='red', size=4, jitter=True)

            annotator = Annotator(ax, model_pairs, x="ROI",
                                  y="intensity", data=df_mean_all_analyte)
            formatted_pvalues = [f'{significance(p)}' for p in pvalue]
            annotator.set_custom_annotations(formatted_pvalues)
            annotator.annotate()

            plt.title(f'm/z {self.mz.iloc[int(analyte)]}')
            plt.xlabel('ROI')
            plt.ylabel('Mean Intensity')
            plt.tight_layout()
            plt.savefig(
                f"{self.data_dir}/figures/boxplot/mz_{self.mz[int(analyte)]:.3f}.jpg", dpi=200)
            plt.show()
