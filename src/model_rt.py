from utils_preprocess import energy_arrays, signal_interval
import numpy as np
from tqdm import tqdm

class RealtimeModel():
    def __init__(self, model, classes = {"Clean": 0, 
                            "Narrowband Start": 1, 
                            "Narrowband Stop": 2, 
                            "Wideband Start": 3, 
                            "Wideband Stop": 4}, 
                            class_map = {0: "Clean", 
                            1: "Narrowband", 
                            2: "Clean", 
                            3: "Wideband", 
                            4: "Clean"}, nfft=1024, offset=4, n_shifts=1, n_partitions=32):
        """
        Use a energy diff vector classifier to do real time predictions over a stream of data.

        model: Energy vector classifier. Must have a .pretict(X) method.
        nfft: Size of the fft transformation, recommended to be the same as the one used to train the model
        offset: Number of intervals to skip to calculate the energy diff vectors
        n_shifts: Number of overlapping shifted intervals, used to detect 
                  the signal faster in cost of extra computation. It must divide nfft.
        n_partitions: Size of the energy diff vector, must be the same as the one used to train the model.
        """
        assert n_shifts==1, "n_shifts!=1 not supported yet"
        self.n_confirmations = max(1, offset-2)
        self.model = model
        self.nfft = nfft
        self.n_partitions = n_partitions
        self.num_intervals = offset+1
        if n_shifts > 1:
            self.num_intervals += 1
        self.buffer = np.empty((nfft*self.num_intervals), dtype=np.complex)
        self.buffer_index = 0
        self.ready = False
        self.offset = offset
        self.fd_buffer = np.empty((nfft, offset, n_shifts), dtype=np.float64)
        self.n_shifts = n_shifts
        self.predictions = np.empty((nfft, n_shifts), dtype=np.int)
        self.classes = classes
        self.class_map = class_map
        self.prediction = classes["Clean"]
        self.intervals_since_last_prediction = 0
        self.intervals_since_current_prediction = 0
        self.possible_prediction = classes["Clean"]

    def reset(self):
        """
        Cleans and resets the current state of the system.
        Useful when receiving a new signal or the current prediction is wrong.
        """
        self.__init__(self.model, self.classes, self.class_map, self.nfft, self.offset, self.n_shifts, self.n_partitions)

    def get_current_prediction(self, X:np.array):
        """
        Takes nfft new samples

        returns the prediction and the number of samples it passed since that prediction helds true
        """

        # All the buffers are circular buffers

        self.intervals_since_last_prediction += 1
        self.intervals_since_current_prediction += 1

        if self.buffer_index == (self.num_intervals-1):
            self.ready = True

        start = self.buffer_index*self.nfft
        end = (self.buffer_index+1)*self.nfft
        self.buffer[start:end] = X

        if self.ready:
            for i in range(self.n_shifts): # Note: it can be optimized
                shift = i*(self.nfft//self.n_shifts)
                self.fd_buffer[:, :, i] = energy_arrays(
                                            signal_interval(
                                                    np.concatenate(
                                                            (self.buffer[((end+shift)%self.buffer.shape[0]):],
                                                            self.buffer[:1+((end+shift)%self.buffer.shape[0])])
                                                        ), 
                                                    self.buffer.shape[0],
                                                    self.nfft
                                                ), 
                                            self.n_partitions, 
                                            offset=self.offset
                                        )
                self.predictions[:, i] = self.model.predict(self.fd_buffer[:, :, i])

            # The current implementation only considers the first shift
            prediction = self.predictions[-1, 0]

            if prediction != self.classes["Clean"]:
                if self.possible_prediction != prediction:
                    self.intervals_since_current_prediction = 0
                self.possible_prediction = prediction 

                # TODO: what if the end class is different from the start class?
                if np.all(self.predictions[-self.n_confirmations:, 0] == self.predictions[-1, 0]):
                    self.prediction = prediction
                    self.intervals_since_last_prediction = self.intervals_since_current_prediction
            
        self.buffer_index = (self.buffer_index+1) % self.num_intervals

        return self.class_map[self.prediction], self.intervals_since_last_prediction*self.nfft

    def classificate_recordings(self, recordings: list) -> list:
        """
        Takes a list of np.array[np.complex] rf recordings,
        classificates each one and predicts near which sample
        the anomaly started.
        """

        prediction = [None]*len(recordings)
        for i, signal in tqdm(enumerate(recordings)):
            energy_dif = energy_arrays(
                            signal_interval(signal, signal.shape[0], self.nfft), 
                            self.n_partitions, 
                            offset=self.offset
                        )
            y = self.model.predict(energy_dif)
            shift = self.n_confirmations-1
            key = y!=self.classes["Clean"]
            
            # Makes sure that the "not Clean" intervals are repeated at least "shift" times.
            # This should make the model more robust and it improve the accuracy
            for j in range(1,shift+1):
                # each iteration "key" looses one interval
                key = key[:-1] & (y[j:] == y[:-j]) 

            if np.any(key): # Not clean
                unique, index = np.unique(y[key], return_index=True)

                # takes the first anomaly detected as the prediction
                prediction = unique[index.argmin()] 

                # takes the anomaly end
                end_prediction = unique[index.argmax()] 

                # If the classification is not correct or there are multiple predictions, 
                # assume the signal is clean.
                # WARNING: The real time model may behave differently because it may not classify the signal as clean.
                if self.class_map[prediction] != self.class_map[end_prediction] or len(unique)!=2:
                    prediction = self.class_map[self.classes["Clean"]] # TODO: maybe we should classify it as unkown anomaly
                    start, end = None, None
                else:
                    # considers the first interval classified to such prediction as the start
                    # and the last interval classified to such prediction end as the end
                    start = y==prediction
                    end = y==end_prediction
                    for j in range(1,shift+1):
                        # each iteration "start" and "end" looses one interval
                        repeated = (y[j:] == y[:-j])
                        start = start[:-1] & repeated
                        end = end[:-1] & repeated
                    start = start.argmax()
                    end = end.argmax()

                #unique, index, counts = np.unique(y, return_index=True, return_counts=True)

                prediction[i] = {"Class":prediction, "Start":start, "Stop":end}

            else: # Clean signal
                prediction[i] = {"Class":self.classes["Clean"], "Start":None, "Stop":None}

        return prediction
            

        
    
    
    

